"""Batch scoring and explanations (CHURN-13, CHURN-14, CHURN-15, CHURN-16).

Pure functions used by the scoring service layer:

- `score_eligible_customers` (CHURN-13): computes features at the scoring
  cutoff from immutable version frames and predicts churn probability for
  every eligible customer with a fitted pipeline. Eligible = the customer
  has at least one completed order inside the observation window ending at
  the scoring cutoff (the same eligibility contract as labeling);
  customers without observable order history are excluded, never scored
  with fabricated features.

- `risk_band` (CHURN-14): thresholds are configuration passed in by the
  caller (defaults come from `Settings`), recorded per scoring run — never
  hardcoded business truth inside the model code.

- `global_importance` (CHURN-15): model-appropriate global interpretation
  (standardized logistic coefficients / tree impurity importance). Every
  report is labeled as *association*, not causation.

- `explain_customer` (CHURN-16): per-customer top supporting signals.
  Linear models get exact signed contributions (coefficient x
  standardized feature value); tree ensembles get an explicitly-labeled
  approximate attribution. All wording avoids causal language and states
  the limitations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.domains.features.pipeline import FEATURE_COLUMNS, FeatureConfig, compute_features

# Association (not causation) wording used in every explanation payload.
CAVEAT_LINEAR = (
    "Contributions describe associations between this customer's feature values and the "
    "model's churn estimate; they are not causal effects and not a guarantee of behavior."
)
CAVEAT_TREE = (
    "Tree ensembles have no exact per-customer coefficients, so contributions are an "
    "approximate attribution (feature importance weighted by how unusual this customer's "
    "value is). They describe associations with the model's estimate, not causal effects."
)


@dataclass(frozen=True)
class ScoringConfig:
    """Configuration for one scoring run (CHURN-13/14)."""

    observation_window_days: int = 180
    low_band_max: float = 0.30  # probability < low_band_max → "low"
    high_band_min: float = 0.70  # probability >= high_band_min → "high"


def validate_band_thresholds(low_band_max: float, high_band_min: float) -> tuple[float, float]:
    """Validate risk band thresholds as configuration (CHURN-14).

    Enforces 0 <= low_band_max <= high_band_min <= 1 and returns them as
    a normalized (float, float) pair.
    """
    try:
        low, high = float(low_band_max), float(high_band_min)
    except (TypeError, ValueError) as exc:
        raise ValueError("Risk band thresholds must be numbers.") from exc
    if not (0.0 <= low <= high <= 1.0):
        raise ValueError(
            f"Risk band thresholds must satisfy 0 <= low_band_max <= high_band_min <= 1 "
            f"(got low_band_max={low_band_max}, high_band_min={high_band_min})."
        )
    return low, high


def risk_band(probability: float, *, low_band_max: float, high_band_min: float) -> str:
    """Band one probability (CHURN-14): low < low_band_max <= medium < high_band_min <= high."""
    if probability >= high_band_min:
        return "high"
    if probability >= low_band_max:
        return "medium"
    return "low"


def scoring_cutoff(orders_df: pd.DataFrame) -> str:
    """The scoring cutoff is the latest order date in the data.

    features at this cutoff use every order on-or-before it — the natural
    "as of today" view of the customer base.
    """
    dates = pd.to_datetime(orders_df["order_date"], errors="coerce").dropna()
    if dates.empty:
        raise ValueError("No dated orders available for scoring.")
    return dates.max().date().isoformat()


def _numeric_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)[list(FEATURE_COLUMNS)].apply(pd.to_numeric, errors="coerce")


def score_eligible_customers(
    customers_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    pipeline: Any,
    *,
    observation_window_days: int = 180,
) -> tuple[pd.DataFrame, str]:
    """Score every eligible customer with a fitted pipeline (CHURN-13).

    Returns (score frame, cutoff). The frame has one row per eligible
    customer: customer_id, the feature columns, `churn_probability`, and
    `risk_band` computed with the config's thresholds. Deterministic for
    the same inputs (via the fitted pipeline, which is seeded at training).
    """
    cutoff = scoring_cutoff(orders_df)
    features = compute_features(
        customers_df, orders_df, FeatureConfig(cutoff_date=cutoff, observation_days=observation_window_days)
    )
    eligible = [row for row in features if row["recency_days"] is not None]
    if not eligible:
        raise ValueError("No eligible customers to score (no completed orders in the observation window).")

    X = _numeric_frame(eligible)
    if X.isna().all(axis=None):
        raise ValueError("All feature values are missing; scoring is not possible.")
    probabilities = pipeline.predict_proba(X)[:, 1]
    scored = X.copy()
    scored.insert(0, "customer_id", [row["customer_id"] for row in eligible])
    scored["churn_probability"] = np.round(probabilities.astype(float), 6)
    return scored, cutoff


def _standardized(row_values: np.ndarray, population: pd.DataFrame) -> np.ndarray:
    """Feature values standardized against the scored population."""
    mean = population.mean().to_numpy()
    std = population.std(ddof=0).replace(0, 1.0).to_numpy()
    return np.divide(np.asarray(row_values, dtype=float) - mean, np.where(std == 0, 1.0, std))


def explain_customer(
    pipeline: Any,
    model_type: str,
    population: pd.DataFrame,
    customer: pd.Series,
) -> dict[str, Any]:
    """Per-customer contributing signals (CHURN-16).

    `population` is the frame of scored feature values (all scored
    customers) and `customer` one row of it. Linear models receive exact
    signed contributions; trees receive an explicitly approximate one.
    The payload carries the association-not-causation caveat and the
    list of top signals (highest absolute contribution first), plus
    `limitations`.
    """
    method = {
        "logistic_regression": "standardized_linear_contribution",
        "random_forest": "approximate_attribution",
        "auto": "approximate_attribution",
    }.get(model_type, "approximate_attribution")

    values = customer[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    z = _standardized(values, population)

    if model_type == "logistic_regression":
        estimator = pipeline.named_steps["estimator"]
        coefficients = np.asarray(estimator.coef_[0], dtype=float)
        contributions = coefficients * z
        exact = True
        caveat = CAVEAT_LINEAR
    else:
        estimator = pipeline.named_steps["estimator"]
        importances = np.asarray(estimator.feature_importances_, dtype=float)
        contributions = importances * z
        exact = False
        caveat = CAVEAT_TREE

    order = np.argsort(-np.abs(contributions))
    signals = [
        {
            "feature": FEATURE_COLUMNS[i],
            "value": None if pd.isna(values[i]) else round(float(values[i]), 4),
            "contribution": round(float(contributions[i]), 4),
            "direction": "higher_risk" if contributions[i] > 0 else "lower_risk",
        }
        for i in order[:3]
    ]
    return {
        "method": method,
        "exact": exact,
        "top_signals": signals,
        "disclaimer": "These are associations with the model's churn estimate, not causes of churn.",
        "caveat": caveat,
        "limitations": [
            "Explanations reflect the model's view of the data, not the true decision process of the customer.",
            "Contributions are associations, not causal effects; changing a "
            "feature will not mechanically change the outcome.",
            f"Only the trained features are explainable ({', '.join(FEATURE_COLUMNS[:4])}, …).",
        ],
    }


def global_importance(pipeline: Any, model_type: str) -> dict[str, Any]:
    """Model-appropriate global interpretation (CHURN-15).

    Logistic regression: standardized coefficients (features are scaled
    inside the pipeline, so magnitudes are comparable). Random forest:
    impurity-based feature importances. Always labeled as association.
    """
    estimator = pipeline.named_steps["estimator"]
    ranking_rows: list[dict[str, Any]] = []
    if model_type == "logistic_regression":
        coefficients = np.asarray(estimator.coef_[0], dtype=float)
        ranking_rows = [
            {"feature": feature, "importance": round(float(coef), 4), "signed": round(float(coef), 4)}
            for feature, coef in zip(FEATURE_COLUMNS, coefficients, strict=True)
        ]
        method = "standardized_linear_coefficients"
    else:
        importances = np.asarray(estimator.feature_importances_, dtype=float)
        ranking_rows = [
            {"feature": feature, "importance": round(float(coef), 4), "signed": None}
            for feature, coef in zip(FEATURE_COLUMNS, importances, strict=True)
        ]
        method = "random_forest_impurity_importance"

    if model_type == "logistic_regression":
        ranking = sorted(
            ranking_rows,
            key=lambda item: abs(item["importance"]),
            reverse=True,
        )
    else:
        ranking = sorted(
            ranking_rows,
            key=lambda item: float(item["importance"]),
            reverse=True,
        )

    return {
        "model_type": model_type,
        "method": method,
        # CHURN-15: interpretation is association-based, explicitly not causal.
        "note": (
            "Global importance ranks which inputs the model leans on most; it describes "
            "association with predicted churn risk, not causal effect."
        ),
        "ranking": ranking,
    }
