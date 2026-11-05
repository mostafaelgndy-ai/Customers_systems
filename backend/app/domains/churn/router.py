"""Churn labeling and scoring API (CHURN-01..04, CHURN-13..17).

Endpoints:
  POST /api/v1/churn/labels        — generate + persist labels for a version
  GET  /api/v1/churn/labels        — list stored labels (paginated)
  GET  /api/v1/churn/distribution  — stored class distribution report
  GET  /api/v1/churn/schema        — label schema documentation
  POST /api/v1/churn/scores        — batch-score eligible customers from a model run
  GET  /api/v1/churn/scores        — list scores for a model run (paginated)
  GET  /api/v1/churn/scores/{customer_id} — one customer's score + explanation
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domains.churn import service as churn_service
from app.domains.datasets.models import DatasetVersion
from app.domains.identity.deps import role_checker
from app.domains.identity.models import Membership

router = APIRouter(prefix="/churn", tags=["churn"])

_LABEL_ROLES = role_checker("owner", "admin", "analyst")


class LabelRequest(BaseModel):
    """Request payload for label generation (CHURN-01: configuration stored)."""

    dataset_id: uuid.UUID
    dataset_version_id: uuid.UUID
    cutoff_date: str
    observation_window_days: int = 180
    label_horizon_days: int = 90


class ScoreRequest(BaseModel):
    """CHURN-13/14: score from a succeeded model run; bands configurable."""

    model_run_id: uuid.UUID
    low_band_max: float | None = None  # defaults from Settings (CHURN-14)
    high_band_min: float | None = None

    @field_validator("low_band_max", "high_band_min")
    @classmethod
    def _finite(cls, value: float | None) -> float | None:
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError("Risk band thresholds must be between 0 and 1.")
        return value


class TrainingRequest(BaseModel):
    """Churn training run request (CHURN-05..12; API-06)."""

    dataset_version_id: uuid.UUID
    model_type: str = "auto"  # auto | logistic_regression | random_forest
    observation_window_days: int = 180
    label_horizon_days: int = 90
    random_seed: int = 42
    min_period_customers: int = 30


def _label_schema(row: Any) -> dict[str, Any]:
    return {
        "customer_id": row.customer_id,
        "label": row.label,
        "days_since_last_order": row.days_since_last_order,
        "cutoff_date": row.cutoff_date,
        "observation_window_days": row.observation_window_days,
        "label_horizon_days": row.label_horizon_days,
    }


@router.post("/labels", status_code=201)
def generate_labels_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_LABEL_ROLES),
    request: LabelRequest,
) -> dict[str, Any]:
    """Generate churn labels for a dataset version (CHURN-02, CHURN-03).

    Replacing any previously stored label set for the same
    (cutoff, horizon) configuration; the response carries the class
    distribution (CHURN-04).
    """
    distribution = churn_service.generate_and_persist_labels(
        db,
        org_id=membership.organization_id,
        user_id=membership.user_id,
        dataset_id=request.dataset_id,
        dataset_version_id=request.dataset_version_id,
        cutoff_date=request.cutoff_date,
        observation_window_days=request.observation_window_days,
        label_horizon_days=request.label_horizon_days,
    )
    return {"distribution": distribution}


@router.get("/labels")
def list_labels_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_LABEL_ROLES),
    dataset_version_id: uuid.UUID,
    cutoff_date: str | None = Query(None),
    label_horizon_days: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict[str, Any]:
    """List stored churn labels for a dataset version."""
    items, total = churn_service.list_labels(
        db,
        membership.organization_id,
        dataset_version_id,
        cutoff_date=cutoff_date,
        label_horizon_days=label_horizon_days,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [_label_schema(r) for r in items],
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "total": total,
    }


@router.get("/distribution")
def distribution_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_LABEL_ROLES),
    dataset_version_id: uuid.UUID,
    cutoff_date: str | None = Query(None),
    label_horizon_days: int | None = Query(None),
) -> dict[str, Any]:
    """CHURN-04: class distribution of stored labels, before any training."""
    return {
        "distribution": churn_service.stored_distribution(
            db,
            membership.organization_id,
            dataset_version_id,
            cutoff_date=cutoff_date,
            label_horizon_days=label_horizon_days,
        )
    }


@router.get("/schema")
def schema_endpoint() -> dict[str, Any]:
    """Documented churn label schema."""
    return {"schema": churn_service.label_schema()}


# --- model run registry + training API (CHURN-17, API-06) ---------------------


def _run_schema(db: Session, run: Any) -> dict[str, Any]:
    # Human-friendly version label (L3 bug fix): the run's dataset version
    # number, org-scoped so cross-tenant ids can never leak a label.
    version_number = db.scalar(
        select(DatasetVersion.version_number).where(
            DatasetVersion.id == run.dataset_version_id,
            DatasetVersion.organization_id == run.organization_id,
        )
    )
    return {
        "id": str(run.id),
        "dataset_version_id": str(run.dataset_version_id),
        "version_number": version_number,
        "model_type": run.model_type,
        "status": run.status,
        "observation_window_days": run.observation_window_days,
        "label_horizon_days": run.label_horizon_days,
        "train_cutoff_date": run.train_cutoff_date,
        "metrics": run.metrics,
        "error": run.error,
    }


@router.post("/model-runs", status_code=202)
def create_model_run_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_LABEL_ROLES),
    request: TrainingRequest,
) -> dict[str, Any]:
    """Queue a churn training run (CHURN-05..12; API-06 analysis runs)."""
    run = churn_service.create_model_run(
        db,
        org_id=membership.organization_id,
        user_id=membership.user_id,
        dataset_version_id=request.dataset_version_id,
        model_type=request.model_type,
        observation_window_days=request.observation_window_days,
        label_horizon_days=request.label_horizon_days,
        random_seed=request.random_seed,
        min_period_customers=request.min_period_customers,
    )
    return {"run": _run_schema(db, run)}


@router.get("/model-runs")
def list_model_runs_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_LABEL_ROLES),
    dataset_version_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None, pattern="^(queued|running|succeeded|failed)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict[str, Any]:
    """List training runs (CHURN-17 registry), newest first."""
    runs = churn_service.list_model_runs(
        db,
        membership.organization_id,
        dataset_version_id=dataset_version_id,
        status=status,
    )
    total = len(runs)
    items = runs[(page - 1) * page_size : page * page_size]
    return {
        "items": [_run_schema(db, run) for run in items],
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "total": total,
    }


@router.get("/model-runs/{run_id}")
def get_model_run_endpoint(
    run_id: uuid.UUID,
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_LABEL_ROLES),
) -> dict[str, Any]:
    """One training run's status, metrics, and traceability (CHURN-17)."""
    run = churn_service.get_model_run(db, membership.organization_id, run_id)
    return {"run": _run_schema(db, run)}


# --- scoring endpoints (CHURN-13..17) -----------------------------------------


@router.post("/scores", status_code=202)
def create_scores_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_LABEL_ROLES),
    request: ScoreRequest,
) -> dict[str, Any]:
    """Queue batch scoring from a succeeded model run (CHURN-13).

    Risk band thresholds default to configuration; explicit values are
    validated and recorded with the scoring run (CHURN-14).
    """
    job = churn_service.create_score_run(
        db,
        org_id=membership.organization_id,
        user_id=membership.user_id,
        model_run_id=request.model_run_id,
        low_band_max=request.low_band_max,
        high_band_min=request.high_band_min,
    )
    return {
        "job": {
            "id": str(job.id),
            "job_type": job.job_type,
            "status": job.status,
            "payload": job.payload,
        }
    }


@router.get("/scores")
def list_scores_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_LABEL_ROLES),
    model_run_id: uuid.UUID,
    risk_band: str | None = Query(None, pattern="^(low|medium|high)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict[str, Any]:
    """List stored scores for a model run (CHURN-13/17: traceable)."""
    items, total = churn_service.list_scores(
        db,
        membership.organization_id,
        model_run_id,
        risk_band=risk_band,
        order=order,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [
            {
                "customer_id": row.customer_id,
                "churn_probability": row.churn_probability,
                "risk_band": row.risk_band,
                "cutoff_date": row.cutoff_date,
                "model_run_id": str(row.model_run_id),
            }
            for row in items
        ],
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "total": total,
    }


@router.get("/scores/{customer_id}")
def customer_score_endpoint(
    customer_id: str,
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_LABEL_ROLES),
    model_run_id: uuid.UUID,
) -> dict[str, Any]:
    """One customer's score with association-based explanations (CHURN-16)."""
    return churn_service.get_customer_score(
        db,
        membership.organization_id,
        model_run_id=model_run_id,
        customer_id=customer_id,
    )
