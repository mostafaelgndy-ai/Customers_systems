"""Segmentation run services (SEG-01..10).

- Runs are queued through the shared job infrastructure and executed
  inline (dev/tests) or by the Celery worker (staging/prod).
- Execution fits preprocessing + K-Means, evaluates the DBSCAN
  alternative, persists the artifacts (fitted preprocessing + model +
  metadata), stores profiles/candidate report/warnings on the run row
  and one assignment per customer (SEG-09).
- `compare_segmentation_runs` produces a cross-run stability/comparison
  report with explicit caveats (SEG-10); comparisons are over descriptive
  statistics, never presented as statistical significance.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import joblib
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import APIError
from app.domains.datasets.models import VERSION_VALID
from app.domains.datasets.service import (
    get_scoped_version_by_id,
    load_version_frames,
)
from app.domains.features.pipeline import FEATURE_VERSION
from app.domains.identity.deps import role_checker
from app.domains.jobs.queue import enqueue
from app.domains.segmentation.models import (
    SEGMENTATION_RUN_FAILED,
    SEGMENTATION_RUN_QUEUED,
    SEGMENTATION_RUN_RUNNING,
    SEGMENTATION_RUN_SUCCEEDED,
    SegmentAssignment,
    SegmentationRun,
)
from app.domains.segmentation.pipeline import (
    SEGMENT_FEATURES,
    SegmentationConfig,
    build_profiles,
    relabel_segment,
    run_segmentation,
)

_SEGMENTATION_ROLES = role_checker("owner", "admin", "analyst")


# --- run lifecycle -------------------------------------------------------------


def create_segmentation_run(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    observation_window_days: int = 180,
    k_candidates: tuple[int, ...] | None = None,
    random_seed: int = 42,
    min_cluster_size: int = 5,
) -> SegmentationRun:
    """Queue a segmentation run (SEG-04/09)."""
    version = get_scoped_version_by_id(db, org_id, dataset_version_id)
    if version.status != VERSION_VALID:
        raise APIError(
            f"Dataset version is '{version.status}'; must be 'valid' to segment.",
            409,
            code="CONFLICT",
        )
    if observation_window_days < 1 or min_cluster_size < 1:
        raise APIError("observation_window_days and min_cluster_size must be positive.", 400, code="BAD_REQUEST")
    if random_seed < 0:
        raise APIError("random_seed must be a non-negative integer.", 400, code="BAD_REQUEST")

    config_params: dict[str, Any] = {
        "observation_window_days": observation_window_days,
        "random_seed": random_seed,
        "min_cluster_size": min_cluster_size,
        "k_candidates": list(k_candidates) if k_candidates is not None else None,
        "feature_version": FEATURE_VERSION,
        "features": list(SEGMENT_FEATURES),
    }
    run = SegmentationRun(
        organization_id=org_id,
        dataset_version_id=dataset_version_id,
        status=SEGMENTATION_RUN_QUEUED,
        observation_window_days=observation_window_days,
        random_seed=random_seed,
        params=config_params,
        created_by_user_id=user_id,
    )
    db.add(run)
    db.flush()

    enqueue(
        db,
        job_type="run_segmentation",
        organization_id=org_id,
        created_by_user_id=user_id,
        payload={"run_id": str(run.id)},
    )
    db.commit()
    return run


def execute_segmentation_run(db: Session, *, run_id: uuid.UUID) -> dict[str, Any]:
    """Execute a queued/failed segmentation run (SEG-02..09)."""
    run = db.scalar(select(SegmentationRun).where(SegmentationRun.id == run_id))
    if run is None:
        raise APIError("Segmentation run not found.", 404, code="NOT_FOUND")
    if run.status == SEGMENTATION_RUN_RUNNING:
        raise APIError("Segmentation run is currently running.", 409, code="CONFLICT")
    # Re-running a succeeded/failed run replaces its results (idempotent).

    run.status = SEGMENTATION_RUN_RUNNING
    db.commit()

    params = run.params or {}
    version = get_scoped_version_by_id(db, run.organization_id, run.dataset_version_id)
    customers_df, orders_df = load_version_frames(db, version)

    k_candidates = params.get("k_candidates")
    config = SegmentationConfig(
        observation_window_days=run.observation_window_days,
        k_candidates=tuple(k_candidates) if k_candidates else config_default_candidates(),
        random_seed=run.random_seed,
        min_cluster_size=int(params.get("min_cluster_size", 5)),
    )

    try:
        result = run_segmentation(customers_df, orders_df, config)
    except ValueError as exc:
        run.status = SEGMENTATION_RUN_FAILED
        run.error = str(exc)[:500]
        db.commit()
        raise APIError(f"Segmentation failed: {exc}", 409, code="SEGMENTATION_FAILED") from exc

    artifacts = save_segmentation_artifacts(
        result, Path(settings.models_dir) / "segmentation" / str(run.organization_id) / str(run.id)
    )

    profiles, labels = build_profiles(result)
    run.cutoff_date = result.population.cutoff_date
    run.selected_k = result.selected_k
    run.params = {
        **params,  # SEG-04: seed/init/k recorded with the run
        "kmeans_n_init": config.kmeans_n_init,
        "selected_k": result.selected_k,
        "retained_alternative": result.dbscan_report.get("retained_method"),
    }
    run.profiles = {"segments": profiles, "labels_editable": True}
    run.candidate_report = {"candidates": result.candidate_report, "dbscan": result.dbscan_report}
    run.warnings = {"items": result.warnings}
    run.artifact_path = str(artifacts["directory"])
    run.status = SEGMENTATION_RUN_SUCCEEDED
    run.error = None

    # SEG-09: replace any previous assignments for this run (idempotent)
    # so each customer appears exactly once per run.
    previous = db.scalars(
        select(SegmentAssignment).where(
            SegmentAssignment.organization_id == run.organization_id,
            SegmentAssignment.segmentation_run_id == run.id,
        )
    ).all()
    for row in previous:
        db.delete(row)
    db.flush()  # deletes must land before re-inserting the same unique keys

    frame = result.population.frame.copy()
    frame["cluster_id"] = result.labels
    assignments = [
        SegmentAssignment(
            organization_id=run.organization_id,
            segmentation_run_id=run.id,
            customer_id=row["customer_id"],
            cluster_id=int(row["cluster_id"]),
            segment_label=labels[int(row["cluster_id"])],
            cutoff_date=result.population.cutoff_date,
            created_by_user_id=run.created_by_user_id,
        )
        for row in frame.to_dict("records")
    ]
    db.add_all(assignments)
    db.commit()

    return {
        "run_id": str(run.id),
        "status": SEGMENTATION_RUN_SUCCEEDED,
        "selected_k": result.selected_k,
        "cutoff_date": result.population.cutoff_date,
        "customer_count": int(len(frame)),
        "cluster_sizes": [profile["count"] for profile in profiles],
        "warnings": result.warnings,
        "dbscan_decision": result.dbscan_report.get("decision_note"),
        "artifacts": artifacts,
    }


def config_default_candidates() -> tuple[int, ...]:
    return SegmentationConfig().k_candidates


def save_segmentation_artifacts(result: Any, directory: Path) -> dict[str, Any]:
    """SEG-02/03/09: fitted preprocessing + kmeans + metadata.json."""
    directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(result.preprocessing, directory / "preprocessing.joblib")
    joblib.dump(result.kmeans, directory / "kmeans.joblib")
    metadata = {
        "selected_k": result.selected_k,
        "random_seed": result.config.random_seed,
        "kmeans_n_init": result.config.kmeans_n_init,
        "k_candidates": list(result.config.k_candidates),
        "min_cluster_size": result.config.min_cluster_size,
        "segment_features": list(SEGMENT_FEATURES),
        "cutoff_date": result.population.cutoff_date,
        "candidate_report": result.candidate_report,
        "warnings": result.warnings,
        "dbscan_report": result.dbscan_report,
        "population": {
            "customer_count": int(len(result.population.frame)),
            "feature_medians": {
                feature: None
                if result.population.frame[feature].dropna().empty
                else round(float(result.population.frame[feature].median()), 2)
                for feature in SEGMENT_FEATURES
            },
        },
    }
    (directory / "metadata.json").write_text(
        json.dumps(metadata, indent=2, default=str),
        encoding="utf-8",
    )
    return {"directory": str(directory), "files": ["preprocessing.joblib", "kmeans.joblib", "metadata.json"]}


# --- reads ----------------------------------------------------------------------


def get_segmentation_run(db: Session, org_id: uuid.UUID, run_id: uuid.UUID) -> SegmentationRun:
    run = db.scalar(
        select(SegmentationRun).where(
            SegmentationRun.id == run_id,
            SegmentationRun.organization_id == org_id,
        )
    )
    if run is None:
        raise APIError("Segmentation run not found.", 404, code="NOT_FOUND")
    return run


def list_assignments(
    db: Session,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    cluster_id: int | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[SegmentAssignment], int]:
    """Stored assignments for one run (SEG-09: traceable)."""
    get_segmentation_run(db, org_id, run_id)  # scoped 404 check
    query = select(SegmentAssignment).where(
        SegmentAssignment.organization_id == org_id,
        SegmentAssignment.segmentation_run_id == run_id,
    )
    if cluster_id is not None:
        query = query.where(SegmentAssignment.cluster_id == cluster_id)
    query = query.order_by(SegmentAssignment.customer_id)
    rows = list(db.scalars(query).all())
    return rows[(page - 1) * page_size : page * page_size], len(rows)


def rename_segment(
    db: Session,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    cluster_id: int,
    new_label: str,
) -> dict[str, Any]:
    """SEG-08: descriptive labels are editable and decoupled from cluster ids."""
    run = get_segmentation_run(db, org_id, run_id)
    if run.status != SEGMENTATION_RUN_SUCCEEDED:
        raise APIError(f"Segmentation run status is '{run.status}'.", 409, code="CONFLICT")
    # Rebuild profile dicts as copies so SQLAlchemy's plain JSON column sees a
    # genuinely different value (in-place mutation is not reliably persisted).
    segments = [dict(profile) for profile in (run.profiles or {}).get("segments", [])]
    try:
        relabel_segment(segments, cluster_id, new_label)
    except KeyError as exc:
        raise APIError(str(exc), 404, code="NOT_FOUND") from exc
    except ValueError as exc:
        raise APIError(str(exc), 400, code="BAD_REQUEST") from exc

    run.profiles = {"segments": segments, "labels_editable": True}
    assignments = db.scalars(
        select(SegmentAssignment).where(
            SegmentAssignment.organization_id == org_id,
            SegmentAssignment.segmentation_run_id == run_id,
            SegmentAssignment.cluster_id == cluster_id,
        )
    ).all()
    for assignment in assignments:
        assignment.segment_label = new_label.strip()
    db.commit()
    return {"cluster_id": cluster_id, "segment_label": new_label.strip(), "updated_assignments": len(assignments)}


# --- SEG-10: comparison across runs ----------------------------------------------


def compare_segmentation_runs(
    db: Session,
    org_id: uuid.UUID,
    run_a_id: uuid.UUID,
    run_b_id: uuid.UUID,
) -> dict[str, Any]:
    """Stability/comparison report for two runs of the same version (SEG-10).

    Purely descriptive: compares segment shares, counts, profiles; never
    framed as statistical significance; carries caveats about cutoff
    change, parameters change, and small populations.
    """
    run_a = get_segmentation_run(db, org_id, run_a_id)
    run_b = get_segmentation_run(db, org_id, run_b_id)
    if run_a.status != SEGMENTATION_RUN_SUCCEEDED or run_b.status != SEGMENTATION_RUN_SUCCEEDED:
        raise APIError("Comparison requires two 'succeeded' runs.", 409, code="CONFLICT")

    def _shares(run: SegmentationRun) -> dict[int, float]:
        assignments = db.scalars(
            select(SegmentAssignment).where(
                SegmentAssignment.organization_id == org_id,
                SegmentAssignment.segmentation_run_id == run.id,
            )
        ).all()
        if not assignments:
            return {}
        counts: dict[int, int] = {}
        for assignment in assignments:
            counts[assignment.cluster_id] = counts.get(assignment.cluster_id, 0) + 1
        total = len(assignments)
        return {cluster_id: round(count / total, 4) for cluster_id, count in counts.items()}

    shares_a, shares_b = _shares(run_a), _shares(run_b)
    union_ids = sorted(set(shares_a) | set(shares_b))
    deltas = {
        str(cluster_id): {
            "run_a": shares_a.get(cluster_id),
            "run_b": shares_b.get(cluster_id),
            "change": (
                None
                if shares_a.get(cluster_id) is None or shares_b.get(cluster_id) is None
                else round(shares_b[cluster_id] - shares_a[cluster_id], 4)
            ),
        }
        for cluster_id in union_ids
    }
    max_shift = max(
        (abs(delta["change"]) for delta in deltas.values() if delta["change"] is not None),
        default=0.0,
    )
    caveats = [
        "Sizes are descriptive counts, not a statistical test of significance.",
        "Differences can reflect data changes (newer cutoff) as much as model/seed changes.",
        "Very small runs can swing shares substantially; interpret deltas against counts.",
    ]
    if run_a.cutoff_date != run_b.cutoff_date:
        caveats.append(
            f"Cutoffs differ: run A={run_a.cutoff_date}, run B={run_b.cutoff_date} — data windows are not identical."
        )
    if (run_a.params or {}).get("selected_k") != (run_b.params or {}).get("selected_k"):
        caveats.append("Selected k differs between runs; segment shares are not directly comparable.")
    return {
        "run_a": str(run_a.id),
        "run_b": str(run_b.id),
        "segment_share_deltas": deltas,
        "max_share_shift": round(float(max_shift), 4),
        "caveats": caveats,
    }
