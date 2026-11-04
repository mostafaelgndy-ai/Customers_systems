"""Churn labeling, model training, and scoring services (CHURN-01..17).

- Labels: one (cutoff, horizon) configuration produces one replaceable
  label set per dataset version; re-generating is idempotent
  (CHURN-02..04).
- Model runs: a training run derives its own time-aware split, trains
  the LR baseline and RF benchmark, evaluates, and stores the selected
  model artifact plus full metadata (CHURN-05..12).
- Scoring: a scoring run batches predictions for every eligible customer
  from a succeeded model run's immutable artifact, applies configured
  risk bands, and stores per-customer explanations (CHURN-13..16). Every
  stored score references its `churn_model_runs` row (model version) and
  the immutable Job row records the scoring analysis run itself
  (CHURN-17).
"""

from __future__ import annotations

import uuid
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import APIError
from app.domains.churn.labeling import LabelConfig, generate_labels, label_distribution
from app.domains.churn.models import (
    MODEL_RUN_FAILED,
    MODEL_RUN_QUEUED,
    MODEL_RUN_RUNNING,
    MODEL_RUN_SUCCEEDED,
    ChurnLabel,
    ChurnModelRun,
    ChurnScore,
)
from app.domains.churn.scoring import (
    explain_customer,
    risk_band,
    score_eligible_customers,
    validate_band_thresholds,
)
from app.domains.churn.trainer import TrainingConfig, save_artifacts, train_churn_models
from app.domains.datasets.models import VERSION_VALID
from app.domains.datasets.service import (
    get_scoped_dataset,
    get_scoped_version,
    get_scoped_version_by_id,
    load_version_frames,
)
from app.domains.features.pipeline import FEATURE_VERSION
from app.domains.identity.deps import role_checker
from app.domains.jobs.queue import enqueue

_LABEL_ROLES = role_checker("owner", "admin", "analyst")


def generate_and_persist_labels(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    dataset_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    cutoff_date: str,
    observation_window_days: int = 180,
    label_horizon_days: int = 90,
) -> dict[str, Any]:
    """Generate churn labels for a dataset version and replace the
    previous label set for the same (cutoff, horizon) configuration
    (CHURN-03; idempotent re-runs).

    The generated rows record the complete label configuration
    (observation window, horizon, cutoff) for traceability (CHURN-01).
    """
    dataset = get_scoped_dataset(db, org_id, dataset_id)
    version = get_scoped_version(db, org_id, dataset, dataset_version_id)
    if version.status != VERSION_VALID:
        raise APIError(
            f"Dataset version is '{version.status}'; must be 'valid' to generate labels.",
            409,
            code="CONFLICT",
        )
    try:
        datetime.fromisoformat(cutoff_date)
    except ValueError as exc:
        raise APIError("Invalid cutoff_date; use ISO format (YYYY-MM-DD).", 400, code="BAD_REQUEST") from exc
    if observation_window_days < 1 or label_horizon_days < 1:
        raise APIError("observation_window_days and label_horizon_days must be positive.", 400, code="BAD_REQUEST")

    customers_df, orders_df = load_version_frames(db, version)
    config = LabelConfig(
        cutoff_date=cutoff_date,
        observation_window_days=observation_window_days,
        label_horizon_days=label_horizon_days,
    )
    labels = generate_labels(customers_df, orders_df, config)

    # Idempotent replace for the same (version, cutoff, horizon) config.
    previous = db.scalars(
        select(ChurnLabel).where(
            ChurnLabel.organization_id == org_id,
            ChurnLabel.dataset_version_id == dataset_version_id,
            ChurnLabel.cutoff_date == cutoff_date,
            ChurnLabel.label_horizon_days == label_horizon_days,
        )
    ).all()
    for row in previous:
        db.delete(row)

    rows = [
        ChurnLabel(
            organization_id=org_id,
            dataset_version_id=dataset_version_id,
            customer_id=row["customer_id"],
            cutoff_date=cutoff_date,
            observation_window_days=observation_window_days,
            label_horizon_days=label_horizon_days,
            label=row["label"],
            days_since_last_order=row["days_since_last_order"],
            created_by_user_id=user_id,
        )
        for row in labels
    ]
    db.add_all(rows)
    db.commit()

    distribution = label_distribution(labels)
    distribution["dataset_version_id"] = str(dataset_version_id)
    distribution["cutoff_date"] = cutoff_date
    return distribution


def _stored_labels(
    db: Session,
    org_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    *,
    cutoff_date: str | None = None,
    label_horizon_days: int | None = None,
) -> list[ChurnLabel]:
    query = select(ChurnLabel).where(
        ChurnLabel.organization_id == org_id,
        ChurnLabel.dataset_version_id == dataset_version_id,
    )
    if cutoff_date is not None:
        query = query.where(ChurnLabel.cutoff_date == cutoff_date)
    if label_horizon_days is not None:
        query = query.where(ChurnLabel.label_horizon_days == label_horizon_days)
    query = query.order_by(ChurnLabel.created_at.desc(), ChurnLabel.customer_id)
    return list(db.scalars(query).all())


def stored_distribution(
    db: Session,
    org_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    *,
    cutoff_date: str | None = None,
    label_horizon_days: int | None = None,
) -> dict[str, Any]:
    """CHURN-04: report the persisted class distribution before training."""
    rows = _stored_labels(
        db, org_id, dataset_version_id, cutoff_date=cutoff_date, label_horizon_days=label_horizon_days
    )
    if not rows:
        raise APIError(
            "No churn labels are stored for this dataset version; POST /churn/labels first.",
            404,
            code="NO_LABELS",
        )
    labels = [
        {
            "customer_id": row.customer_id,
            "label": row.label,
            "days_since_last_order": row.days_since_last_order,
        }
        for row in rows
    ]
    dist = label_distribution(labels)
    dist["dataset_version_id"] = str(dataset_version_id)
    dist["cutoff_date"] = rows[0].cutoff_date
    dist["observation_window_days"] = rows[0].observation_window_days
    dist["label_horizon_days"] = rows[0].label_horizon_days
    return dist


def list_labels(
    db: Session,
    org_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    *,
    cutoff_date: str | None = None,
    label_horizon_days: int | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[ChurnLabel], int]:
    rows = _stored_labels(
        db, org_id, dataset_version_id, cutoff_date=cutoff_date, label_horizon_days=label_horizon_days
    )
    total = len(rows)
    items = rows[(page - 1) * page_size : page * page_size]
    return items, total


def label_schema() -> dict[str, Any]:
    """Documented churn-label schema (for contracts and reports)."""
    return {
        "label": {
            "values": ["churned", "active", "ineligible"],
            "definition": (
                "'churned' if the customer has no eligible (completed) order "
                "inside the label horizon after the cutoff; 'active' if they do; "
                "'ineligible' when the customer has no eligible order inside the "
                "observation window and therefore cannot carry a valid label."
            ),
        },
        "cutoff_date": {
            "type": "date",
            "definition": "Last day of the observation period; future data is used only for the label outcome.",
        },
        "observation_window_days": {
            "type": "integer",
            "default": 180,
            "definition": "Length of the observed history period before the cutoff.",
        },
        "label_horizon_days": {
            "type": "integer",
            "default": 90,
            "definition": "Length of the future inactivity window after the cutoff.",
        },
        "days_since_last_order": {
            "type": "integer|null",
            "definition": "Days from the cutoff to the customer's latest eligible order inside the observation window.",
        },
    }


# --- model training runs (CHURN-05..12) --------------------------------------


def create_model_run(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    model_type: str = "auto",
    observation_window_days: int = 180,
    label_horizon_days: int = 90,
    random_seed: int = 42,
    min_period_customers: int = 30,
) -> ChurnModelRun:
    """Queue a churn training run (CHURN-05..12).

    The run records the training window configuration in its columns
    (CHURN-01) and its dataset version for traceability. `model_type` may
    force a candidate; 'auto' lets validation ranking decide.
    """
    version = get_scoped_version_by_id(db, org_id, dataset_version_id)
    if version.status != VERSION_VALID:
        raise APIError(
            f"Dataset version is '{version.status}'; must be 'valid' to train.",
            409,
            code="CONFLICT",
        )
    if observation_window_days < 1 or label_horizon_days < 1:
        raise APIError("observation_window_days and label_horizon_days must be positive.", 400, code="BAD_REQUEST")
    if model_type not in ("auto", "logistic_regression", "random_forest"):
        raise APIError(
            "model_type must be 'auto', 'logistic_regression', or 'random_forest'.",
            400,
            code="BAD_REQUEST",
        )

    run = ChurnModelRun(
        organization_id=org_id,
        dataset_version_id=dataset_version_id,
        model_type=model_type,
        observation_window_days=observation_window_days,
        label_horizon_days=label_horizon_days,
        train_cutoff_date="",  # filled once the split is derived
        status=MODEL_RUN_QUEUED,
        created_by_user_id=user_id,
        metrics={
            "params": {
                "random_seed": random_seed,
                "min_period_customers": min_period_customers,
                "requested_by": str(user_id),
                "model_type_requested": model_type,
            }
        },
    )
    db.add(run)
    db.flush()

    # Queue background training (inline mode executes synchronously).
    enqueue(
        db,
        job_type="train_churn_model",
        organization_id=org_id,
        created_by_user_id=user_id,
        payload={"run_id": str(run.id), "dataset_version_id": str(dataset_version_id)},
    )
    from app.domains.audit.service import record_audit  # SEC-06: sensitive action

    record_audit(
        db,
        org_id=org_id,
        user_id=user_id,
        action="churn.run_requested",
        resource_type="churn_model_run",
        resource_id=run.id,
        details={"model_type": run.model_type, "dataset_version_id": str(dataset_version_id)},
    )
    db.commit()
    return run


def execute_model_run(db: Session, *, run_id: uuid.UUID) -> dict[str, Any]:
    """Execute a queued/failed training run (CHURN-05..12).

    Loads the version's data, derives the time-aware split, trains both
    candidates, evaluates, saves artifacts and metadata, and records the
    run outcome in churn_model_runs. Idempotent: re-running persists to
    the same artifact directory.
    """
    run = db.scalar(select(ChurnModelRun).where(ChurnModelRun.id == run_id))
    if run is None:
        raise APIError("Model run not found.", 404, code="NOT_FOUND")
    if run.status not in (MODEL_RUN_QUEUED, MODEL_RUN_FAILED):
        raise APIError(f"Model run is already '{run.status}'.", 409, code="CONFLICT")

    run.status = MODEL_RUN_RUNNING
    db.commit()

    try:
        version = get_scoped_version_by_id(db, run.organization_id, run.dataset_version_id)
        customers_df, orders_df = load_version_frames(db, version)
        params = ((run.metrics or {}).get("params", {})) if run.metrics else {}

        config = TrainingConfig(
            observation_window_days=run.observation_window_days,
            label_horizon_days=run.label_horizon_days,
            random_seed=params.get("random_seed", 42),
            min_period_customers=params.get("min_period_customers", 30),
            override_selection=run.model_type if run.model_type != "auto" else None,
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = train_churn_models(
                customers_df,
                orders_df,
                config,
                dataset_version_id=str(run.dataset_version_id),
            )
    except Exception as exc:  # noqa: BLE001 - run must never stay stuck 'running'
        # JOB-09: a failed training run records its failure explicitly and
        # never touches a previously persisted artifact/score state.
        run.status = MODEL_RUN_FAILED
        run.error = str(exc)[:500]
        db.commit()
        raise APIError(f"Training failed: {str(exc)[:300]}", 409, code="TRAINING_FAILED") from exc

    artifact_dir = Path(settings.models_dir) / str(run.organization_id) / str(run.id)
    paths = save_artifacts(result, artifact_dir)

    selected = result.evaluation["selection"]["selected_model"]
    run.train_cutoff_date = result.periods.by_name()["test"].cutoff_date
    run.status = MODEL_RUN_SUCCEEDED
    run.metrics = {**result.evaluation, "params": params}
    run.artifact_path = str(artifact_dir)
    if run.model_type == "auto":
        run.model_type = selected
    db.commit()

    return {
        "run_id": str(run.id),
        "status": run.status,
        "model_type": run.model_type,
        "selection": result.evaluation["selection"],
        "split": result.evaluation["split"],
        "artifact_path": run.artifact_path,
        "artifact_files": paths,
        "warnings": result.metadata["warnings"],
    }


def list_model_runs(
    db: Session,
    org_id: uuid.UUID,
    *,
    dataset_version_id: uuid.UUID | None = None,
    status: str | None = None,
) -> list[ChurnModelRun]:
    """Org-scoped model runs (API-06/CHURN-17 registry), newest first."""
    query = select(ChurnModelRun).where(ChurnModelRun.organization_id == org_id)
    if dataset_version_id is not None:
        query = query.where(ChurnModelRun.dataset_version_id == dataset_version_id)
    if status is not None:
        query = query.where(ChurnModelRun.status == status)
    return list(db.scalars(query.order_by(ChurnModelRun.created_at.desc())).all())


def get_model_run(db: Session, org_id: uuid.UUID, run_id: uuid.UUID) -> ChurnModelRun:
    run = db.scalar(
        select(ChurnModelRun).where(
            ChurnModelRun.id == run_id,
            ChurnModelRun.organization_id == org_id,
        )
    )
    if run is None:
        raise APIError("Model run not found.", 404, code="NOT_FOUND")
    return run


# --- batch scoring (CHURN-13, CHURN-14, CHURN-16, CHURN-17) --------------------


def create_score_run(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    model_run_id: uuid.UUID,
    low_band_max: float | None = None,
    high_band_min: float | None = None,
):
    """Queue a scoring run against a succeeded model run (CHURN-13).

    Risk band thresholds default to `Settings` values (CHURN-14:
    configuration, not hardcoded business truth); explicit values must
    satisfy 0 <= low_band_max <= high_band_min <= 1. The Job row returned
    is the immutable analysis-run record the scores refer to (CHURN-17).
    """
    run = get_model_run(db, org_id, model_run_id)
    if run.status != MODEL_RUN_SUCCEEDED or not run.artifact_path:
        raise APIError(
            f"Model run status is '{run.status}'; scoring requires a 'succeeded' run.",
            409,
            code="CONFLICT",
        )
    low = settings.risk_band_low_max if low_band_max is None else low_band_max
    high = settings.risk_band_high_min if high_band_min is None else high_band_min
    try:
        low, high = validate_band_thresholds(low, high)
    except ValueError as exc:
        raise APIError(str(exc), 400, code="BAD_REQUEST") from exc

    job = enqueue(
        db,
        job_type="score_churn_model",
        organization_id=org_id,
        created_by_user_id=user_id,
        payload={
            "model_run_id": str(model_run_id),
            "low_band_max": low,
            "high_band_min": high,
        },
    )
    from app.domains.audit.service import record_audit  # SEC-06: sensitive action

    record_audit(
        db,
        org_id=org_id,
        user_id=user_id,
        action="churn.score_requested",
        resource_type="job",
        resource_id=job.id,
        details={"model_run_id": str(model_run_id), "low_band_max": low, "high_band_min": high},
    )
    db.commit()
    return job


def execute_score_run(
    db: Session,
    *,
    model_run_id: uuid.UUID,
    low_band_max: float = 0.30,
    high_band_min: float = 0.70,
    scoring_run_id: str | None = None,
) -> dict[str, Any]:
    """Execute a scoring run (CHURN-13..17).

    Loads the succeeded model run's persisted pipeline, computes features
    at the data cutoff, scores eligible customers, applies the configured
    risk bands, and replaces the previous scores for this run (idempotent,
    like labels). Response carries counts per band and the traceability
    ids (CHURN-17).
    """
    from app.domains.churn.trainer import FEATURE_COLUMNS  # single source of truth

    low, high = validate_band_thresholds(low_band_max, high_band_min)
    run = db.scalar(select(ChurnModelRun).where(ChurnModelRun.id == model_run_id))
    if run is None:
        raise APIError("Model run not found.", 404, code="NOT_FOUND")
    if run.status != MODEL_RUN_SUCCEEDED or not run.artifact_path:
        raise APIError(f"Model run status is '{run.status}'; cannot score.", 409, code="CONFLICT")

    artifact_file = Path(run.artifact_path) / f"{run.model_type}.joblib"
    if not artifact_file.exists():
        raise APIError(
            f"Model artifact '{run.model_type}.joblib' is missing for this run.",
            409,
            code="CONFLICT",
        )

    import joblib

    pipeline = joblib.load(artifact_file)
    version = get_scoped_version_by_id(db, run.organization_id, run.dataset_version_id)
    customers_df, orders_df = load_version_frames(db, version)

    scored, cutoff = score_eligible_customers(
        customers_df,
        orders_df,
        pipeline,
        observation_window_days=run.observation_window_days,
    )
    population = scored[list(FEATURE_COLUMNS)]

    # Idempotent replace: one prediction per eligible customer per run.
    previous = db.scalars(
        select(ChurnScore).where(
            ChurnScore.organization_id == run.organization_id,
            ChurnScore.model_run_id == run.id,
        )
    ).all()
    for row in previous:
        db.delete(row)
    db.flush()  # deletes must land before re-inserting the same unique keys

    band_counts = {"low": 0, "medium": 0, "high": 0}
    new_rows: list[ChurnScore] = []
    for row in scored.to_dict("records"):
        probability = float(row["churn_probability"])
        band = risk_band(probability, low_band_max=low, high_band_min=high)
        band_counts[band] += 1
        explanation = explain_customer(pipeline, run.model_type, population, pd.Series(row))
        new_rows.append(
            ChurnScore(
                organization_id=run.organization_id,
                model_run_id=run.id,
                customer_id=row["customer_id"],
                churn_probability=probability,
                risk_band=band,
                explanation=explanation,
                cutoff_date=cutoff,
                created_by_user_id=run.created_by_user_id,
            )
        )
    db.add_all(new_rows)
    db.commit()

    return {
        "model_run_id": str(run.id),
        "model_type": run.model_type,
        "model_version": str(run.id),  # CHURN-17: artifact identity = run identity
        "dataset_version_id": str(run.dataset_version_id),
        "scoring_run_id": scoring_run_id,
        "feature_version": FEATURE_VERSION,
        "scoring_cutoff": cutoff,
        "scored": len(new_rows),
        "band_counts": band_counts,
        "risk_bands": {"low_band_max": low, "high_band_min": high, "source": "configuration"},
        "features_used": list(FEATURE_COLUMNS),
        "observation_window_days": run.observation_window_days,
    }


def list_scores(
    db: Session,
    org_id: uuid.UUID,
    model_run_id: uuid.UUID,
    *,
    risk_band: str | None = None,
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[ChurnScore], int]:
    """Stored scores for one model run, ordered by probability (API-11)."""
    if order not in ("asc", "desc"):
        raise APIError("order must be 'asc' or 'desc'.", 400, code="BAD_REQUEST")
    query = select(ChurnScore).where(
        ChurnScore.organization_id == org_id,
        ChurnScore.model_run_id == model_run_id,
    )
    if risk_band is not None:
        query = query.where(ChurnScore.risk_band == risk_band)
    direction = ChurnScore.churn_probability.desc() if order == "desc" else ChurnScore.churn_probability.asc()
    query = query.order_by(direction, ChurnScore.customer_id)
    rows = list(db.scalars(query).all())
    total = len(rows)
    return rows[(page - 1) * page_size : page * page_size], total


def get_customer_score(
    db: Session,
    org_id: uuid.UUID,
    *,
    model_run_id: uuid.UUID,
    customer_id: str,
) -> dict[str, Any]:
    """Customer-level score + explanation view (CHURN-16)."""
    score = db.scalar(
        select(ChurnScore).where(
            ChurnScore.organization_id == org_id,
            ChurnScore.model_run_id == model_run_id,
            ChurnScore.customer_id == customer_id,
        )
    )
    if score is None:
        raise APIError("Score not found for this customer and model run.", 404, code="NOT_FOUND")
    return {
        "customer_id": score.customer_id,
        "model_run_id": str(score.model_run_id),
        "churn_probability": score.churn_probability,
        "risk_band": score.risk_band,
        "cutoff_date": score.cutoff_date,
        "explanation": score.explanation or {},
        "traceability": {
            "model_run_id": str(score.model_run_id),
            "note": "Every score references an immutable model run and its versioned artifact.",
        },
    }
