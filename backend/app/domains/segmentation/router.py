"""Customer segmentation API (SEG-01..10 groundwork for API-08).

Endpoints:
  POST /api/v1/segmentation/runs                  — queue a segmentation run
  GET  /api/v1/segmentation/runs/{run_id}          — run detail (params, profiles, candidates, warnings)
  GET  /api/v1/segmentation/runs/{run_id}/assignments — paginated assignments
  POST /api/v1/segmentation/runs/{run_id}/segments/{cluster_id}/rename — editable descriptive labels
  GET  /api/v1/segmentation/compare?run_a_id&run_b_id — cross-run comparison report
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domains.identity.deps import role_checker
from app.domains.identity.models import Membership
from app.domains.segmentation import service as segmentation_service

router = APIRouter(prefix="/segmentation", tags=["segmentation"])

_ROLES = role_checker("owner", "admin", "analyst")


class SegmentationRequest(BaseModel):
    dataset_version_id: uuid.UUID
    observation_window_days: int = 180
    k_candidates: list[int] | None = None  # SEG-05 bounded, validated sweep
    random_seed: int = 42
    min_cluster_size: int = 5

    @field_validator("k_candidates")
    @classmethod
    def _bounded(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        if not (1 <= len(value) <= 6) or any(not 2 <= k <= 10 for k in value):
            raise ValueError("k_candidates must contain 1–6 values, each between 2 and 10.")
        return value


class SegmentRenameRequest(BaseModel):
    segment_label: str


@router.get("/runs")
def list_runs_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_ROLES),
    status: str | None = Query(None, pattern="^(queued|running|succeeded|failed)$"),
    dataset_version_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict[str, Any]:
    """List segmentation runs (API-06/UI-11), newest first."""
    runs = segmentation_service.list_segmentation_runs(
        db,
        membership.organization_id,
        status=status,
        dataset_version_id=dataset_version_id,
    )
    total = len(runs)
    items = runs[(page - 1) * page_size : page * page_size]
    return {
        "items": [_run_schema(run)["run"] for run in items],
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "total": total,
    }


@router.post("/runs", status_code=202)
def create_run_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_ROLES),
    request: SegmentationRequest,
) -> dict[str, Any]:
    run = segmentation_service.create_segmentation_run(
        db,
        org_id=membership.organization_id,
        user_id=membership.user_id,
        dataset_version_id=request.dataset_version_id,
        observation_window_days=request.observation_window_days,
        k_candidates=tuple(request.k_candidates) if request.k_candidates else None,
        random_seed=request.random_seed,
        min_cluster_size=request.min_cluster_size,
    )
    return _run_schema(run)


@router.get("/runs/{run_id}")
def get_run_endpoint(
    run_id: uuid.UUID,
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_ROLES),
) -> dict[str, Any]:
    run = segmentation_service.get_segmentation_run(db, membership.organization_id, run_id)
    return _run_schema(run)


@router.get("/runs/{run_id}/assignments")
def assignments_endpoint(
    run_id: uuid.UUID,
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_ROLES),
    cluster_id: int | None = Query(None, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict[str, Any]:
    items, total = segmentation_service.list_assignments(
        db,
        membership.organization_id,
        run_id,
        cluster_id=cluster_id,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [
            {
                "customer_id": row.customer_id,
                "cluster_id": row.cluster_id,
                "segment_label": row.segment_label,
                "cutoff_date": row.cutoff_date,
                "segmentation_run_id": str(row.segmentation_run_id),
            }
            for row in items
        ],
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "total": total,
    }


@router.post("/runs/{run_id}/segments/{cluster_id}/rename")
def rename_segment_endpoint(
    run_id: uuid.UUID,
    cluster_id: int,
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_ROLES),
    request: SegmentRenameRequest,
) -> dict[str, Any]:
    return segmentation_service.rename_segment(
        db,
        membership.organization_id,
        run_id,
        cluster_id=cluster_id,
        new_label=request.segment_label,
    )


@router.get("/compare")
def compare_runs_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_ROLES),
    run_a_id: uuid.UUID,
    run_b_id: uuid.UUID,
) -> dict[str, Any]:
    return segmentation_service.compare_segmentation_runs(db, membership.organization_id, run_a_id, run_b_id)


def _run_schema(run: Any) -> dict[str, Any]:
    return {
        "run": {
            "id": str(run.id),
            "dataset_version_id": str(run.dataset_version_id),
            "status": run.status,
            "observation_window_days": run.observation_window_days,
            "random_seed": run.random_seed,
            "selected_k": run.selected_k,
            "cutoff_date": run.cutoff_date,
            "params": run.params,
            "profiles": run.profiles,
            "candidate_report": run.candidate_report,
            "warnings": run.warnings,
            "error": run.error,
        }
    }
