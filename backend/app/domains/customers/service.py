"""Customer detail services (API-09)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.churn.models import (
    MODEL_RUN_SUCCEEDED,
    ChurnModelRun,
    ChurnScore,
)
from app.domains.datasets.service import get_scoped_version_by_id
from app.domains.segmentation.models import (
    SEGMENTATION_RUN_SUCCEEDED,
    SegmentAssignment,
    SegmentationRun,
)


def get_customer_detail(
    db: Session,
    *,
    org_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    customer_id: str,
) -> dict[str, Any]:
    """Unified customer view for one dataset version (API-09).

    Combines the latest succeeded churn scoring for the customer (score +
    association-based explanation) and the latest succeeded segmentation
    assignment, each traceable to its producing run. Absent analyses are
    reported as null, not errors, so the endpoint still works before any
    churn/segmentation analysis has been run.
    """
    get_scoped_version_by_id(db, org_id, dataset_version_id)  # scoped 404 guard

    score = _latest_score(db, org_id, dataset_version_id, customer_id)
    assignment = _latest_assignment(db, org_id, customer_id)

    data_version = "no_data"
    if score and assignment:
        data_version = "score+segment"
    elif score:
        data_version = "score_only"
    elif assignment:
        data_version = "segment_only"

    return {
        "customer_id": customer_id,
        "dataset_version_id": str(dataset_version_id),
        "data_version": data_version,
        "churn": score,
        "segment": assignment,
        "note": (
            "Churn probability and segment labels are model estimates over "
            "historical behavior; explanations are associations, not causes."
        ),
    }


def _latest_score(
    db: Session,
    org_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    customer_id: str,
) -> dict[str, Any] | None:
    """Newest score for the customer from the newest succeeded run on the version."""
    run_ids = db.scalars(
        select(ChurnModelRun.id)
        .where(
            ChurnModelRun.organization_id == org_id,
            ChurnModelRun.dataset_version_id == dataset_version_id,
            ChurnModelRun.status == MODEL_RUN_SUCCEEDED,
        )
        .order_by(ChurnModelRun.created_at.desc())
    ).all()
    if not run_ids:
        return None
    score = db.scalar(
        select(ChurnScore)
        .where(
            ChurnScore.organization_id == org_id,
            ChurnScore.customer_id == customer_id,
            ChurnScore.model_run_id.in_(run_ids),
        )
        .order_by(ChurnScore.created_at.desc())
    )
    if score is None:
        return None
    return {
        "model_run_id": str(score.model_run_id),
        "customer_id": score.customer_id,
        "churn_probability": score.churn_probability,
        "risk_band": score.risk_band,
        "cutoff_date": score.cutoff_date,
        "explanation": score.explanation or {},
    }


def _latest_assignment(
    db: Session,
    org_id: uuid.UUID,
    customer_id: str,
) -> dict[str, Any] | None:
    """Newest segment assignment from the newest succeeded segmentation run."""
    run_ids = db.scalars(
        select(SegmentationRun.id)
        .where(
            SegmentationRun.organization_id == org_id,
            SegmentationRun.status == SEGMENTATION_RUN_SUCCEEDED,
        )
        .order_by(SegmentationRun.created_at.desc())
    ).all()
    if not run_ids:
        return None
    assignment = db.scalar(
        select(SegmentAssignment)
        .where(
            SegmentAssignment.organization_id == org_id,
            SegmentAssignment.customer_id == customer_id,
            SegmentAssignment.segmentation_run_id.in_(run_ids),
        )
        .order_by(SegmentAssignment.created_at.desc())
    )
    if assignment is None:
        return None
    return {
        "segmentation_run_id": str(assignment.segmentation_run_id),
        "cluster_id": assignment.cluster_id,
        "segment_label": assignment.segment_label,
        "cutoff_date": assignment.cutoff_date,
    }
