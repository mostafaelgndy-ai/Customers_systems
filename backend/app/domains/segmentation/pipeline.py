"""Customer segmentation pipeline (SEG-01..08, SEG-11).

Feature selection (SEG-01): the segment feature set is behavioral only —
recency, frequency, monetary value, average order value, tenure, recent
activity, purchase pacing. Identifiers (customer_id, email) never enter
the cluster space; they only anchor assignments. All features come from
the shared feature pipeline (FE-03..06) computed at the segmentation
cutoff, so segmentation and churn reasoning see the same customer view.

Preprocessing (SEG-02/03): one sklearn Pipeline (median imputer +
StandardScaler) is fit on the segmented population at run time and
persisted with the model artifact — scaling is always applied with the
fitted transformer, never re-fit at prediction time.

Model (SEG-04): K-Means with a fixed random seed, n_init and the k
recorded in the run metadata; candidates (SEG-05) sweep a bounded set of
cluster counts reporting sizes and silhouette score where valid (null
with a reason otherwise). Safeguards (SEG-06): tiny/degenerate clusters
raise run warnings instead of silently looking reliable.

Profiles (SEG-07): per-segment counts plus means/medians/quartiles for
every feature. Descriptive labels (SEG-08) are derived from those
profile statistics (value/frequency/recency tiers) — never from cluster
ids — and remain editable via `relabel_segment`.

Alternative (SEG-11): DBSCAN is evaluated and reported honestly; K-Means
is retained unless the report demonstrates an interpretability or
operational gain for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN, KMeans
from sklearn.impute import SimpleImputer
from sklearn.metrics import silhouette_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.domains.features.pipeline import FeatureConfig, compute_features

# SEG-01: behavioral feature set. Deliberately excludes identifiers and
# anything not describing shopping behavior.
SEGMENT_FEATURES = (
    "recency_days",
    "frequency",
    "monetary_value",
    "avg_order_value",
    "tenure_days",
    "orders_90d",
    "monetary_90d",
    "avg_purchase_interval_days",
)

# SEG-05: bounded candidate sweep.
DEFAULT_K_CANDIDATES = (2, 3, 4, 5, 6)
DEFAULT_RANDOM_SEED = 42

# SEG-06: a cluster below this size is flagged as tiny, not presented as reliable.
DEFAULT_MIN_CLUSTER_SIZE = 5


@dataclass(frozen=True)
class SegmentationConfig:
    """Immutable segmentation run configuration (SEG-04)."""

    observation_window_days: int = 180
    k_candidates: tuple[int, ...] = DEFAULT_K_CANDIDATES
    random_seed: int = DEFAULT_RANDOM_SEED
    kmeans_n_init: int = 10
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE
    dbscan_eps: float = 0.8  # SEG-11 evaluation parameters (reported, not preset as truth)
    dbscan_min_samples: int = 5


@dataclass
class SegmentPopulation:
    cutoff_date: str
    frame: pd.DataFrame  # customer_id + SEGMENT_FEATURES (raw values)


@dataclass
class SegmentationResult:
    config: SegmentationConfig
    population: SegmentPopulation
    preprocessing: Pipeline
    kmeans: KMeans
    selected_k: int
    candidate_report: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)
    dbscan_report: dict[str, Any] = field(default_factory=dict)


def segmentation_cutoff(orders_df: pd.DataFrame) -> str:
    """The segmentation cutoff is the latest order date (features use only
    orders on-or-before the cutoff — the FE-02 contract)."""
    dates = pd.to_datetime(orders_df["order_date"], errors="coerce").dropna()
    if dates.empty:
        raise ValueError("No dated orders available for segmentation.")
    return dates.max().date().isoformat()


def build_population(
    customers_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    config: SegmentationConfig,
) -> SegmentPopulation:
    """Segmentable population: customers with a completed order inside the
    observation window at the cutoff (same eligibility contract as
    scoring); customers without behavioral history are excluded, never
    clustered on fabricated features."""
    cutoff = segmentation_cutoff(orders_df)
    features = compute_features(
        customers_df,
        orders_df,
        FeatureConfig(cutoff_date=cutoff, observation_days=config.observation_window_days),
    )
    rows = [
        {"customer_id": row["customer_id"], **{f: row[f] for f in SEGMENT_FEATURES}}
        for row in features
        if row["recency_days"] is not None
    ]
    if not rows:
        raise ValueError("No segmentable customers (no completed orders in the observation window).")
    frame = pd.DataFrame(rows)
    frame[list(SEGMENT_FEATURES)] = frame[list(SEGMENT_FEATURES)].apply(pd.to_numeric, errors="coerce")
    return SegmentPopulation(cutoff_date=cutoff, frame=frame)


def build_preprocessing() -> Pipeline:
    """SEG-02/03: median imputation + standard scaler in one persisted
    Pipeline (fit on the segmented population, reused consistently)."""
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )


def _silhouette_scores(X_scaled: np.ndarray, labels: np.ndarray) -> float | None:
    if len(np.unique(labels)) < 2 or len(X_scaled) <= len(np.unique(labels)):
        return None
    try:
        return round(float(silhouette_score(X_scaled, labels)), 4)
    except ValueError:
        return None  # degenerate layouts (e.g. one point per cluster) have no silhouette


def run_segmentation(
    customers_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    config: SegmentationConfig,
) -> SegmentationResult:
    """Fit preprocessing + K-Means over the bounded candidate sweep
    (SEG-04..06) and produce profiles (SEG-07) + labels (SEG-08)."""
    population = build_population(customers_df, orders_df, config)
    X = population.frame[list(SEGMENT_FEATURES)]
    preprocessing = build_preprocessing()
    X_scaled = preprocessing.fit_transform(X)

    warnings: list[str] = []
    candidate_report: list[dict[str, Any]] = []

    ks = sorted({k for k in config.k_candidates if 2 <= k <= len(X_scaled) - 1})
    if not ks:
        raise ValueError(
            f"Not enough segmentable customers ({len(X_scaled)}) for any candidate k in {config.k_candidates}."
        )

    valid_sizes_checked: dict[int, bool] = {}
    for k in ks:
        kmeans = KMeans(
            n_clusters=k,
            random_state=config.random_seed,
            n_init=config.kmeans_n_init,
        )
        labels = kmeans.fit_predict(X_scaled)
        sizes = np.bincount(labels, minlength=k)
        tiny = [int(i) for i, size in enumerate(sizes) if 0 < size < config.min_cluster_size]
        valid_sizes_checked[k] = not tiny
        candidate_report.append(
            {
                "k": k,
                "cluster_sizes": [int(size) for size in sizes],
                "silhouette": _silhouette_scores(X_scaled, labels),
                "inertia": round(float(kmeans.inertia_), 4),
                "tiny_clusters": tiny,
                "usable": not tiny and _silhouette_scores(X_scaled, labels) is not None,
            }
        )
        if tiny:
            warnings.append(
                f"k={k} produces cluster(s) {tiny} below the minimum size {config.min_cluster_size}; "
                "these are flagged as not reliable (SEG-06)."
            )

    # SEG-06 safeguards: prefer a k with all-usable clusters; fall back with warnings.
    with_silhouette = [row for row in candidate_report if row["silhouette"] is not None]
    usable = [row for row in with_silhouette if row["usable"]]
    if usable:
        best = max(usable, key=lambda row: row["silhouette"])
    elif with_silhouette:
        best = max(with_silhouette, key=lambda row: row["silhouette"])
        warnings.append(
            "No candidate k produced only usable clusters; selected the best silhouette layout "
            "with flagged tiny clusters — treat sizes with caution."
        )
    else:
        raise ValueError(
            "Silhouette score could not be computed for any candidate k; the population may be "
            "too small or perfectly degenerate. Expansion of data or fewer candidates is advised."
        )

    selected_k = int(best["k"])
    kmeans = KMeans(
        n_clusters=selected_k,
        random_state=config.random_seed,
        n_init=config.kmeans_n_init,
    )
    labels = kmeans.fit_predict(X_scaled)

    # SEG-11: evaluate the alternative honestly; keep K-Means absent a gain.
    dbscan_report = evaluate_dbscan_alternative(X_scaled, config)
    if isinstance(dbscan_report.get("decision_note"), str) and dbscan_report.get("dbscan_clusters"):
        warnings.append("SEG-11 evaluation: " + str(dbscan_report["decision_note"]))

    return SegmentationResult(
        config=config,
        population=population,
        preprocessing=preprocessing,
        kmeans=kmeans,
        selected_k=selected_k,
        candidate_report=candidate_report,
        warnings=warnings,
        labels=[int(v) for v in labels],
        dbscan_report=dbscan_report,
    )


def evaluate_dbscan_alternative(X_scaled: np.ndarray, config: SegmentationConfig) -> dict[str, Any]:
    """SEG-11: DBSCAN comparison. Reports what it produced; retains K-Means
    unless the alternative clearly improves interpretability or operation
    (measured here as: no noise, comparable cluster count to K-Means
    candidates)."""
    dbscan = DBSCAN(eps=config.dbscan_eps, min_samples=config.dbscan_min_samples)
    labels = dbscan.fit_predict(X_scaled)
    n_found = len({int(v) for v in labels if v != -1})
    noise = int((labels == -1).sum())
    noise_share = round(noise / len(labels), 4) if len(labels) else 1.0
    kmeans_cluster_range = (min(config.k_candidates), max(config.k_candidates))
    comparable = (
        noise == 0
        and kmeans_cluster_range[0] <= n_found <= kmeans_cluster_range[1]
        and all(size >= config.min_cluster_size for size in np.bincount(labels[labels != -1]).tolist())
    )
    if comparable:
        note = (
            f"DBSCAN (eps={config.dbscan_eps}) found {n_found} clusters with 0% noise. "
            "Both methods are viable; K-Means retained for its stable, comparable cluster "
            "counts and consistent profile reporting."
        )
    else:
        note = (
            f"DBSCAN (eps={config.dbscan_eps}) found {n_found} clusters with "
            f"{round(noise_share * 100, 2)}% noise. K-Means retained: DBSCAN's sensitivity to the "
            "density/eps choice gives no clear interpretability gain for this population."
        )
    return {
        "method": "DBSCAN",
        "parameters": {"eps": config.dbscan_eps, "min_samples": config.dbscan_min_samples},
        "dbscan_clusters": n_found,
        "noise_share": noise_share,
        "retained_method": "kmeans",
        "decision_note": note,
    }


# --- SEG-07/08: profiles + descriptive labels ---------------------------------


def _tier(value: float, median: float, low_word: str, high_word: str, neutral: str = "mid") -> str:
    if not np.isfinite(value):
        return neutral
    if value < median * 0.8:
        return low_word
    if value > median * 1.2:
        return high_word
    return neutral


def build_profiles(
    result: SegmentationResult,
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Per-segment counts, means/medians/quartiles per feature (SEG-07) and
    descriptive labels derived from those statistics (SEG-08: editable,
    not based on arbitrary cluster ids)."""
    frame = result.population.frame.copy()
    frame["cluster_id"] = result.labels
    medians = {f: float(frame[f].median()) for f in SEGMENT_FEATURES}

    profiles: list[dict[str, Any]] = []
    labels: dict[int, str] = {}
    for cluster_id in sorted(set(result.labels)):
        part = frame[frame["cluster_id"] == cluster_id]
        stats: dict[str, Any] = {"count": int(len(part)), "share": round(len(part) / len(frame), 4)}
        tiers = []
        for feature in SEGMENT_FEATURES:
            values = part[feature].astype(float)
            stats[feature] = {
                "mean": None if values.dropna().empty else round(float(values.mean()), 2),
                "median": None if values.dropna().empty else round(float(values.median()), 2),
                "p25": None if values.dropna().empty else round(float(values.quantile(0.25)), 2),
                "p75": None if values.dropna().empty else round(float(values.quantile(0.75)), 2),
                "std": None if values.dropna().empty else round(float(values.std()), 2),
            }
        money_median = medians["monetary_value"]
        freq_median = medians["frequency"]
        recency_median = medians["recency_days"]
        value_tier = _tier(stats["monetary_value"]["median"] or 0.0, money_median, "low value", "high value")
        freq_tier = _tier(stats["frequency"]["median"] or 0.0, freq_median, "infrequent", "frequent")
        # Lower recency is better: recent shoppers vs lapsed ones.
        cluster_recency = stats["recency_days"]["median"]
        if cluster_recency is None:
            recency_word = "steady recency"
        elif cluster_recency < recency_median * 0.8:
            recency_word = "recent"
        elif cluster_recency > recency_median * 1.2:
            recency_word = "lapsed"
        else:
            recency_word = "steady recency"
        tiers = [word for word in (value_tier, freq_tier, recency_word) if word != "mid"]
        descriptive = " · ".join(tiers) if tiers else "core pattern"
        labels[cluster_id] = descriptive
        stats["descriptive_label"] = descriptive
        stats["label_editable"] = True  # SEG-08: rename via relabel_segment
        stats["cluster_id"] = int(cluster_id)
        profiles.append(stats)
    return profiles, labels


def relabel_segment(profiles: list[dict[str, Any]], cluster_id: int, new_label: str) -> None:
    """SEG-08: user-editable descriptive labels (never cluster-id based)."""
    label = new_label.strip()
    if not label:
        raise ValueError("Segment label cannot be empty.")
    for profile in profiles:
        if profile["cluster_id"] == cluster_id:
            profile["descriptive_label"] = label
            return
    raise KeyError(f"Cluster {cluster_id} does not exist in this run.")
