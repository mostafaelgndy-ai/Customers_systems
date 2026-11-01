"""Churn labeling API (CHURN-01..04).

Endpoints:
  POST /api/v1/churn/labels        — generate + persist labels for a version
  GET  /api/v1/churn/labels        — list stored labels (paginated)
  GET  /api/v1/churn/distribution  — stored class distribution report
  GET  /api/v1/churn/schema        — label schema documentation
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domains.churn import service as churn_service
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
