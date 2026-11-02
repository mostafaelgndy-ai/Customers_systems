"""Churn model training pipeline (CHURN-05..12).

Time-aware training (CHURN-05): observations are built as per-cutoff
snapshots. Three chronologically ordered cutoffs shadow the data —
train, validation, test — spaced one label horizon apart. Features of a
period only use orders on-or-before that period's cutoff (the FE-02/FE-09
contract) and its label uses only outcomes inside (cutoff, cutoff+horizon]
(CHURN-03), so a period's features can never absorb information from a
later period. Customers without a fully observable horizon are excluded
by labeling eligibility, never mislabeled (CHURN-02).

Note on customer repetition: because observation windows overlap across
periods, a customer may appear in train and validation/test as distinct
snapshot observations. This is the standard period-panel design; every
snapshot's features/labels are self-contained to its own cutoff, so no
post-period outcome leaks into any other period (documented in the
artifact metadata as the chosen split design).

Models:
  - logistic_regression (CHURN-06 baseline): median imputation + scaling
    + estimator wrapped in one sklearn Pipeline, saved as one artifact.
  - random_forest (CHURN-07 benchmark): same split and feature contract.

Evaluation (CHURN-08..10): precision/recall/F1/confusion matrix at the
default 0.5 threshold, ROC-AUC and PR-AUC (explicitly null with a reason
when undefined, e.g. a single positive class), threshold trade-off
sweeps plus top-k precision/recall at configured contact capacities
(CHURN-09), and probability calibration summaries (CHURN-10).

Selection (CHURN-11): candidates are compared on validation metrics
(first PR-AUC, then ROC-AUC, then F1) with a written rationale that
names the operational trade-off; an override model type may be forced
by configuration. Both candidates remain fully reported.

Artifacts (CHURN-12): each fitted pipeline is joblib-dumped alongside a
metadata.json with dataset version, feature version, cutoffs, windows,
split sizes, hyperparameters, metrics, random seed and dependency
versions.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, confusion_matrix, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.domains.churn.labeling import LabelConfig, generate_labels
from app.domains.churn.scoring import global_importance
from app.domains.features.pipeline import FEATURE_COLUMNS, FEATURE_VERSION, FeatureConfig, compute_features

PERIOD_NAMES = ("train", "validation", "test")

DEFAULT_SEED = 42

POSITIVE_LABEL = "churned"


@dataclass(frozen=True)
class TrainingConfig:
    """Immutable training configuration (CHURN-05/12)."""

    observation_window_days: int = 180
    label_horizon_days: int = 90
    random_seed: int = DEFAULT_SEED
    n_estimators: int = 150
    min_period_customers: int = 30
    # Fractions of customers reviewed when reporting top-k capacity (CHURN-09).
    topk_fractions: tuple[float, ...] = (0.10, 0.25, 0.50)
    threshold_sweep: tuple[float, ...] = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    # Force selection to a specific model type (CHURN-11). None = auto.
    override_selection: str | None = None


@dataclass
class PeriodData:
    name: str
    cutoff_date: str
    frame: pd.DataFrame  # feature rows + label column


@dataclass
class Periods:
    periods: list[PeriodData]
    total_before_dedup: int = 0
    repeated_customers: int = 0
    warnings: list[str] = field(default_factory=list)

    def by_name(self) -> dict[str, PeriodData]:
        return {p.name: p for p in self.periods}


@dataclass
class TrainingResult:
    periods: Periods
    fitted_pipelines: dict[str, Pipeline]
    evaluation: dict[str, Any]
    selected_model_type: str
    metadata: dict[str, Any]


# --- time-aware split helpers (CHURN-05) -----------------------------------


def _parse_order_dates(orders_df: pd.DataFrame) -> pd.DataFrame:
    df = orders_df.copy()
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce", utc=True).dt.tz_localize(None)
    df = df[df["order_date"].notna()]
    return df


def derive_cutoffs(orders_df: pd.DataFrame, config: TrainingConfig) -> tuple[str, str, str]:
    """Three chronological cutoffs (train, validation, test) spaced one
    label horizon apart; the last one leaves a fully observable horizon
    inside the data."""
    order_dates = _parse_order_dates(orders_df)["order_date"]
    if order_dates.empty:
        raise ValueError("No dated orders to derive cutoffs from.")
    first, last = order_dates.min(), order_dates.max()
    step = timedelta(days=config.label_horizon_days)
    test = last - step
    validation = test - step
    train = validation - step
    if train < first:
        raise ValueError(
            "Dataset time span is too short for a train/validation/test split "
            f"with {config.label_horizon_days}-day label horizons "
            f"(first={first.date().isoformat()}, last={last.date().isoformat()})."
        )
    return (train.date().isoformat(), validation.date().isoformat(), test.date().isoformat())


def build_periods(
    customers_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    config: TrainingConfig,
    cutoffs: tuple[str, str, str] | None = None,
) -> Periods:
    """Build labeled snapshots for each of the three chronological periods.

    Features come from compute_features at the snapshot cutoff (eligible
    orders only), labels from generate_labels at the same cutoff; both use
    only data inside their own boundary (no leakage).
    """
    if cutoffs is None:
        cutoffs = derive_cutoffs(orders_df, config)
    customers_df = customers_df.copy()
    customers_df["customer_id"] = customers_df["customer_id"].astype("string").str.strip()
    orders_df = orders_df.copy()
    orders_df["customer_id"] = orders_df["customer_id"].astype("string").str.strip()

    frames: list[pd.DataFrame] = []
    warnings: list[str] = []
    total = 0

    for name, cutoff in zip(PERIOD_NAMES, cutoffs, strict=True):
        fc = FeatureConfig(
            cutoff_date=cutoff,
            observation_days=config.observation_window_days,
        )
        lc = LabelConfig(
            cutoff_date=cutoff,
            observation_window_days=config.observation_window_days,
            label_horizon_days=config.label_horizon_days,
        )

        features = compute_features(customers_df, orders_df, fc)
        labels = {row["customer_id"]: row for row in generate_labels(customers_df, orders_df, lc)}

        rows = []
        for row in features:
            labeled = labels[row["customer_id"]]
            if labeled["label"] not in ("churned", "active"):
                continue  # CHURN-02: ineligible customers excluded, never mislabeled
            rows.append({**row, "label": labeled["label"]})

        frame = pd.DataFrame(rows)
        total += len(frame)
        if frame.empty:
            warnings.append(f"The {name} period (cutoff {cutoff}) has no labelable customers.")
            continue
        frame.insert(0, "period", name)
        frames.append(frame)

    if not frames:
        raise ValueError("No snapshot with labelable customers in any of the derived periods: " + "; ".join(warnings))

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["customer_id", "cutoff_date"]).reset_index(drop=True)

    # A customer's observation window overlaps adjacent periods; they may
    # contribute a snapshot to several periods (documented panel design).
    repeated = int(combined.groupby("customer_id", dropna=False).size().gt(1).sum())

    periods = []
    for name in PERIOD_NAMES:
        part = combined[combined["period"] == name].reset_index(drop=True)
        if part.empty:
            continue
        periods.append(PeriodData(name=name, cutoff_date=part["cutoff_date"].iloc[0], frame=part))
    ordered = {p.name: p for p in periods}
    ordered_periods = [ordered[name] for name in PERIOD_NAMES if name in ordered]
    return Periods(periods=ordered_periods, total_before_dedup=total, repeated_customers=repeated, warnings=warnings)


# --- model factories (CHURN-06, CHURN-07) -----------------------------------


def build_model_pipelines(config: TrainingConfig) -> dict[str, Pipeline]:
    """One sklearn Pipeline per candidate: preprocessing persisted with the
    estimator (CHURN-06), identical split/feature contract (CHURN-07)."""
    return {
        "logistic_regression": Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                (
                    "estimator",
                    LogisticRegression(max_iter=1000, class_weight="balanced", random_state=config.random_seed),
                ),
            ]
        ),
        "random_forest": Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "estimator",
                    RandomForestClassifier(
                        n_estimators=config.n_estimators,
                        class_weight="balanced_subsample",
                        random_state=config.random_seed,
                        n_jobs=1,  # single-threaded: deterministic and fastest at this scale
                    ),
                ),
            ]
        ),
    }


# --- evaluation helpers (CHURN-08..10) --------------------------------------


def _confusion_counts(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, int]:
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}


def _safe_metrics(y_true: np.ndarray, y_prob: np.ndarray, threshold: float = 0.5) -> dict[str, Any]:
    """Precision/recall/F1/ROC-AUC/PR-AUC/confusion at a threshold.

    Undefined metrics are null with an explicit reason (CHURN-08): no
    positive predictions makes precision undefined, no positive truths
    makes recall undefined, and a single-class y_true makes both AUCs
    undefined.
    """
    y_pred = (y_prob >= threshold).astype(int)
    cm = _confusion_counts(y_true, y_pred)

    precision = None if (cm["tp"] + cm["fp"]) == 0 else cm["tp"] / (cm["tp"] + cm["fp"])
    recall = None if (cm["tp"] + cm["fn"]) == 0 else cm["tp"] / (cm["tp"] + cm["fn"])
    f1 = (
        None
        if precision is None or recall is None or (precision + recall) == 0
        else 2 * precision * recall / (precision + recall)
    )

    reasons: list[str] = []
    if precision is None:
        reasons.append("precision undefined: no positive predictions at this threshold")
    if recall is None:
        reasons.append("recall undefined: no positive labels present")
    roc_auc = pr_auc = None
    if len(np.unique(y_true)) < 2:
        reasons.append(" roc_auc/pr_auc undefined: only one class in y_true")
    else:
        roc_auc = float(roc_auc_score(y_true, y_prob))
        pr_auc = float(average_precision_score(y_true, y_prob))

    result: dict[str, Any] = {
        "threshold": threshold,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "roc_auc": round(roc_auc, 4) if roc_auc is not None else None,
        "pr_auc": round(pr_auc, 4) if pr_auc is not None else None,
        "confusion_matrix": cm,
    }
    if reasons:
        result["undefined_reasons"] = [r.strip() for r in reasons]
    return result


def _threshold_analysis(y_true: np.ndarray, y_prob: np.ndarray, config: TrainingConfig) -> dict[str, Any]:
    """Threshold trade-off sweep + top-k precision/recall at contact
    capacities (CHURN-09)."""
    sweep = []
    for t in config.threshold_sweep:
        y_pred = (y_prob >= t).astype(int)
        n_pred_pos = int(y_pred.sum())
        cm = _confusion_counts(y_true, y_pred)
        precision = None if n_pred_pos == 0 else cm["tp"] / n_pred_pos
        recall = None if (cm["tp"] + cm["fn"]) == 0 else cm["tp"] / (cm["tp"] + cm["fn"])
        sweep.append(
            {
                "threshold": t,
                "precision": round(precision, 4) if precision is not None else None,
                "recall": round(recall, 4) if recall is not None else None,
                "flagged_customers": n_pred_pos,
                "true_churners_flagged": cm["tp"],
                "false_alarms": cm["fp"],
            }
        )

    topk = []
    order = np.argsort(-y_prob)
    total = len(y_true)
    for fraction in config.topk_fractions:
        k = max(1, int(round(fraction * total)))
        selected = order[:k]
        y_pred_k = np.zeros(total, dtype=int)
        y_pred_k[selected] = 1
        cm = _confusion_counts(y_true, y_pred_k)
        precision = cm["tp"] / k
        n_pos = int(y_true.sum())
        recall = cm["tp"] / n_pos if n_pos else None
        topk.append(
            {
                "top_fraction": fraction,
                "k": k,
                "precision_at_k": round(precision, 4),
                "recall_at_k": round(recall, 4) if recall is not None else None,
                "churners_reached": cm["tp"],
            }
        )

    return {
        "sweep": sweep,
        "top_k_by_capacity": topk,
        "note": "Top-k rows approximate contacting the k highest-risk customers (realistic contact capacity).",
    }


def _calibration_summary(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> dict[str, Any]:
    """Brier score + reliability bins (CHURN-10). Reports calibration
    state BEFORE any calibration refit is applied."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins = []
    for i in range(n_bins):
        upper_inclusive = i == n_bins - 1
        mask = (y_prob >= edges[i]) & ((y_prob <= edges[i + 1]) if upper_inclusive else (y_prob < edges[i + 1]))
        count = int(mask.sum())
        if count == 0:
            continue
        bins.append(
            {
                "bin_lower": round(float(edges[i]), 4),
                "bin_upper": round(float(edges[i + 1]), 4),
                "count": count,
                "predicted_mean": round(float(y_prob[mask].mean()), 4),
                "observed_churn_rate": round(float(y_true[mask].mean()), 4),
            }
        )
    brier = float(np.mean((y_prob - y_true) ** 2))
    return {
        "brier_score": round(brier, 4),
        "reliability_bins": bins,
        "n_bins_requested": n_bins,
        "n_bins_populated": len(bins),
    }


def _class_balance(frame: pd.DataFrame) -> dict[str, int]:
    counts = frame["label"].value_counts().to_dict()
    return {label: int(counts.get(label, 0)) for label in ("churned", "active")}


# --- training orchestration (CHURN-05..11) ----------------------------------


def _evaluate_periods(
    periods: Periods, pipelines: dict[str, Pipeline], config: TrainingConfig
) -> tuple[dict[str, Any], dict[str, Pipeline]]:
    """Fit on train, select on validation, and report everything
    deterministically (CHURN-08..11)."""
    train = periods.periods[0]
    validation = periods.periods[1]
    test = periods.periods[2]

    X_train = train.frame[list(FEATURE_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    y_train = (train.frame["label"] == POSITIVE_LABEL).astype(int)

    if y_train.nunique() < 2:
        raise ValueError(
            "Training requires both label classes in the train period "
            "(churned and active); single label class found. Consider a longer "
            "observation window or wider data history."
        )

    fitted, val_probs, test_probs = {}, {}, {}
    for name, pipeline in pipelines.items():
        fitted[name] = pipeline.fit(X_train, y_train)
        val_probs[name] = pipeline.predict_proba(validation.frame[list(FEATURE_COLUMNS)])[:, 1]
        test_probs[name] = pipeline.predict_proba(test.frame[list(FEATURE_COLUMNS)])[:, 1]

    evaluation: dict[str, Any] = {
        "split": {p.name: {"cutoff_date": p.cutoff_date, "customers": int(len(p.frame))} for p in periods.periods},
        "class_balance": {p.name: _class_balance(p.frame) for p in periods.periods},
    }

    def _per_model(probs: dict[str, np.ndarray], frame: pd.DataFrame) -> dict[str, Any]:
        y_true = (frame["label"] == POSITIVE_LABEL).astype(int).to_numpy()
        return {name: _safe_metrics(y_true, prob) for name, prob in probs.items()}

    evaluation["validation"] = _per_model(val_probs, validation.frame)
    evaluation["test"] = _per_model(test_probs, test.frame)
    evaluation["threshold_analysis"] = {
        name: _threshold_analysis((validation.frame["label"] == POSITIVE_LABEL).astype(int).to_numpy(), prob, config)
        for name, prob in val_probs.items()
    }
    evaluation["calibration"] = {
        name: _calibration_summary((validation.frame["label"] == POSITIVE_LABEL).astype(int).to_numpy(), prob)
        for name, prob in val_probs.items()
    }

    # CHURN-15: model-appropriate global interpretation for every candidate.
    evaluation["global_importance"] = {name: global_importance(pipeline, name) for name, pipeline in fitted.items()}

    evaluation["selection"] = _select_model(evaluation["validation"], config)
    return evaluation, fitted


def _select_model(validation_metrics: dict[str, dict[str, Any]], config: TrainingConfig) -> dict[str, Any]:
    """CHURN-11: compare candidates on validation; rationale covers the
    operational trade-off, not accuracy alone."""
    if config.override_selection is not None and config.override_selection in validation_metrics:
        return {
            "selected_model": config.override_selection,
            "selection_basis": "config override",
            "rationale": (
                f"'{config.override_selection}' was forced by configuration; "
                "both candidates remain reported with their full metrics."
            ),
        }

    def _key(name: str) -> tuple[float, float, float, str]:
        metrics = validation_metrics[name]
        pr = metrics["pr_auc"] if metrics["pr_auc"] is not None else -1.0
        roc = metrics["roc_auc"] if metrics["roc_auc"] is not None else -1.0
        f1 = metrics["f1"] if metrics["f1"] is not None else -1.0
        return (pr, roc, f1, "") if name == "logistic_regression" else (pr, roc, f1, "!")

    ranked = sorted(validation_metrics, key=_key, reverse=True)
    selected = ranked[0]
    other = ranked[-1]
    sel = validation_metrics[selected]
    oth = validation_metrics[other]
    rationale = (
        f"'{selected}' selected on validation metrics first by PR-AUC "
        f"({sel['pr_auc']} vs {oth['pr_auc']}); ROC-AUC ({sel['roc_auc']} vs {oth['roc_auc']} "
        f"and F1 ({sel['f1']} vs {oth['f1']}) tie-break."
        " Random forests usually capture non-linear effects but are less"
        " transparent; logistic regression favours auditability. Operational"
        " fit is documented alongside, not replaced by, the metric choice."
    )
    return {
        "selected_model": selected,
        "selection_basis": "validation ranking (PR-AUC, then ROC-AUC, then F1)",
        "rationale": rationale,
    }


# --- artifacts (CHURN-12) ----------------------------------------------------


def _dependency_versions() -> dict[str, str]:
    import sklearn

    return {
        "python": sys.version.split()[0],
        "scikit-learn": sklearn.__version__,
        "joblib": joblib.__version__,
        "pandas": pd.__version__,
        "numpy": np.__version__,
    }


def save_artifacts(result: TrainingResult, directory: Path) -> dict[str, str]:
    """Dump every fitted pipeline plus metadata.json (CHURN-12)."""
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for model_type, pipeline in result.fitted_pipelines.items():
        path = directory / f"{model_type}.joblib"
        joblib.dump(pipeline, path)
        paths[model_type] = str(path)
    metadata_path = directory / "metadata.json"
    metadata_path.write_text(json.dumps(result.metadata, indent=2, sort_keys=True), encoding="utf-8")
    paths["metadata"] = str(metadata_path)
    return paths


# --- entry point --------------------------------------------------------------


def train_churn_models(
    customers_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    config: TrainingConfig,
    *,
    dataset_version_id: str | None = None,
) -> TrainingResult:
    """Time-aware training for one dataset version (CHURN-05..12)."""
    periods = build_periods(customers_df, orders_df, config)
    for period in periods.periods:
        need = config.min_period_customers
        have = len(period.frame)
        if have < need:
            periods.warnings.append(
                f"The {period.name} period has {have} labeled customers (< {need} requested minimum)."
            )
    if len(periods.periods) < len(PERIOD_NAMES):
        raise ValueError(
            "Training requires all three periods (train/validation/test); got only "
            + ", ".join(p.name for p in periods.periods)
            + ". "
            + "; ".join(periods.warnings)
        )

    pipelines = build_model_pipelines(config)
    evaluation, fitted = _evaluate_periods(periods, pipelines, config)
    selected = evaluation["selection"]["selected_model"]

    metadata = {
        "split_design": (
            "period-panel: chronological train/validation/test cutoffs; features never use post-cutoff orders"
        ),
        "dataset_version_id": dataset_version_id,
        "feature_version": FEATURE_VERSION,
        "feature_columns": list(FEATURE_COLUMNS),
        "cutoffs": {p.name: p.cutoff_date for p in periods.periods},
        "observation_window_days": config.observation_window_days,
        "label_horizon_days": config.label_horizon_days,
        "split_sizes": {p.name: int(len(p.frame)) for p in periods.periods},
        "split_class_balance": {p.name: _class_balance(p.frame) for p in periods.periods},
        "hyperparameters": {
            model_type: {
                step: params.get_params() for step, params in (("estimator", pipeline.named_steps["estimator"]),)
            }
            for model_type, pipeline in pipelines.items()
        },
        "random_seed": config.random_seed,
        "threshold_analysis": evaluation["threshold_analysis"],
        "calibration": evaluation["calibration"],
        "global_importance": evaluation["global_importance"],
        "selection": evaluation["selection"],
        "dependency_versions": _dependency_versions(),
        "warnings": periods.warnings,
    }
    return TrainingResult(
        periods=periods,
        fitted_pipelines=pipelines,
        evaluation=evaluation,
        selected_model_type=selected,
        metadata=metadata,
    )


# Ruff-visible import kept for callers that build reports from metrics only.
__all__ = [
    "TrainingConfig",
    "TrainingResult",
    "build_periods",
    "build_model_pipelines",
    "derive_cutoffs",
    "train_churn_models",
    "save_artifacts",
    "FEATURE_COLUMNS",
    "POSITIVE_LABEL",
]
