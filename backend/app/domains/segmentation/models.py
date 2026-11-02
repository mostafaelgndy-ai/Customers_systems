"""Customer segmentation models (SEG-09).

Every segment assignment references the immutable segmentation run
(analysis run) that produced it; the run stores its snapshot of
parameters, profiles, candidate report, and warnings.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import orm
from sqlalchemy.orm import Mapped

from app.db.base import Base, TimestampMixin

# Segmentation run statuses (aligned with job lifecycle).
SEGMENTATION_RUN_QUEUED = "queued"
SEGMENTATION_RUN_RUNNING = "running"
SEGMENTATION_RUN_SUCCEEDED = "succeeded"
SEGMENTATION_RUN_FAILED = "failed"


class SegmentationRun(Base, TimestampMixin):
    """One K-Means segmentation run over a dataset version.

    Parameters, profiles, candidate report, and warnings are stored on
    the run row; assignments live in `segment_assignments`.
    """

    __tablename__ = "segmentation_runs"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    dataset_version_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    status: Mapped[str] = orm.mapped_column(sa.String(20), nullable=False, default=SEGMENTATION_RUN_QUEUED)
    observation_window_days: Mapped[int] = orm.mapped_column(sa.Integer, nullable=False, default=180)
    random_seed: Mapped[int] = orm.mapped_column(sa.Integer, nullable=False, default=42)
    selected_k: Mapped[int | None] = orm.mapped_column(sa.Integer, nullable=True)
    cutoff_date: Mapped[str] = orm.mapped_column(sa.String(32), nullable=False, default="")
    params: Mapped[dict | None] = orm.mapped_column(sa.JSON, nullable=True)  # SEG-04 seed/init/k recording
    profiles: Mapped[dict | None] = orm.mapped_column(sa.JSON, nullable=True)  # SEG-07/08
    candidate_report: Mapped[dict | None] = orm.mapped_column(sa.JSON, nullable=True)  # SEG-05
    warnings: Mapped[dict | None] = orm.mapped_column(sa.JSON, nullable=True)  # SEG-06
    artifact_path: Mapped[str | None] = orm.mapped_column(sa.String(1000), nullable=True)
    error: Mapped[str | None] = orm.mapped_column(sa.Text(), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), nullable=False)


class SegmentAssignment(Base, TimestampMixin):
    """A customer's cluster assignment from one segmentation run (SEG-09)."""

    __tablename__ = "segment_assignments"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    segmentation_run_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    customer_id: Mapped[str] = orm.mapped_column(sa.String(200), nullable=False, index=True)
    cluster_id: Mapped[int] = orm.mapped_column(sa.Integer, nullable=False)
    segment_label: Mapped[str] = orm.mapped_column(sa.String(200), nullable=False)  # editable (SEG-08)
    cutoff_date: Mapped[str] = orm.mapped_column(sa.String(32), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id",
            "segmentation_run_id",
            "customer_id",
            name="uq_segment_assignment_org_run_customer",
        ),
    )
