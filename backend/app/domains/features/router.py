"""Feature engineering API endpoints (Phase 3).

Endpoints:
  POST   /api/v1/features/runs                    — create a feature run
  GET    /api/v1/features/runs                    — list feature runs
  GET    /api/v1/features/runs/{run_id}           — get run status + results
  GET    /api/v1/features/schema                  — canonical feature schema
  POST   /api/v1/features/runs/{run_id}/execute   — execute a queued run
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.db.session import get_db
from app.domains.features.models import (
    FEATURE_RUN_FAILED,
    FEATURE_RUN_QUEUED,
    FeatureRun,
    FeatureSnapshot,
)
from app.domains.features.service import (
    create_feature_run,
    execute_feature_run,
    get_schema,
    list_runs,
)
from app.domains.identity.deps import role_checker
from app.domains.identity.models import Membership

router = APIRouter(prefix="/features", tags=["features"])

_FEATURE_ROLES = role_checker("owner", "admin", "analyst")


def _run_schema(run: FeatureRun, snapshot: FeatureSnapshot | None) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "dataset_version_id": str(run.dataset_version_id),
        "cutoff_date": run.cutoff_date,
        "observation_days": run.observation_days,
        "feature_version": run.feature_version,
        "status": run.status,
        "row_count": run.row_count,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "features": snapshot.features if snapshot else None,
    }


@router.post("/runs", status_code=201)
def create_run(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_FEATURE_ROLES),
    dataset_version_id: uuid.UUID,
    cutoff_date: str,
    observation_days: int = 180,
) -> dict[str, Any]:
    """Create a new feature computation run (FE-08, FE-10).

    Requires a valid dataset version. The run is queued for background
    processing; use GET /runs/{run_id} to check status and retrieve results.
    """
    run = create_feature_run(
        db,
        org_id=membership.organization_id,
        user_id=membership.user_id,
        dataset_version_id=dataset_version_id,
        cutoff_date=cutoff_date,
        observation_days=observation_days,
    )
    return {"run": _run_schema(run, None)}


@router.get("/runs")
def list_runs_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_FEATURE_ROLES),
    dataset_version_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict[str, Any]:
    """List feature runs for the calling organization."""
    runs = list_runs(
        db,
        org_id=membership.organization_id,
        dataset_version_id=dataset_version_id,
    )
    total = len(runs)
    items = runs[(page - 1) * page_size : page * page_size]
    return {
        "items": [_run_schema(r, None) for r in items],
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "total": total,
    }


@router.get("/runs/{run_id}")
def get_run(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_FEATURE_ROLES),
    run_id: uuid.UUID,
) -> dict[str, Any]:
    """Get a feature run's status and results (FE-10)."""
    run = db.scalar(
        sa.select(FeatureRun).where(
            FeatureRun.id == run_id,
            FeatureRun.organization_id == membership.organization_id,
        )
    )
    if run is None:
        raise APIError("Feature run not found.", 404, code="NOT_FOUND")

    snapshot = db.scalar(sa.select(FeatureSnapshot).where(FeatureSnapshot.feature_run_id == run_id))
    return _run_schema(run, snapshot)


@router.post("/runs/{run_id}/execute")
def execute_run(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_FEATURE_ROLES),
    run_id: uuid.UUID,
) -> dict[str, Any]:
    """Execute a queued feature run (FE-08).

    Runs the feature computation synchronously and returns the results.
    In production this is dispatched to a Celery worker.
    """
    run = db.scalar(
        sa.select(FeatureRun).where(
            FeatureRun.id == run_id,
            FeatureRun.organization_id == membership.organization_id,
        )
    )
    if run is None:
        raise APIError("Feature run not found.", 404, code="NOT_FOUND")
    if run.status not in (FEATURE_RUN_QUEUED, FEATURE_RUN_FAILED):
        raise APIError(
            f"Run is already '{run.status}'; cannot execute.",
            409,
            code="CONFLICT",
        )
    result = execute_feature_run(db, run_id=run_id)
    return result


@router.get("/schema")
def get_feature_schema() -> dict[str, Any]:
    """Return the canonical feature schema (FE-07)."""
    return {"features": get_schema(), "feature_version": "1.0.0"}
