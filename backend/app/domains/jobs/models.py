"""Background job lifecycle model (PRD §7.8, JOB tasks)."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy import orm
from sqlalchemy.orm import Mapped

from app.db.base import Base, TimestampMixin

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"

VALIDATE_DATASET = "validate_dataset"
COMPUTE_FEATURES = "compute_features"
TRAIN_CHURN_MODEL = "train_churn_model"
SCORE_CHURN_MODEL = "score_churn_model"
RUN_SEGMENTATION = "run_segmentation"
EXPORT_CSV = "export_csv"
JOB_TYPES = (
    VALIDATE_DATASET,
    COMPUTE_FEATURES,
    TRAIN_CHURN_MODEL,
    SCORE_CHURN_MODEL,
    RUN_SEGMENTATION,
    EXPORT_CSV,
)


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True)
    job_type: Mapped[str] = orm.mapped_column(sa.String(50), nullable=False)
    status: Mapped[str] = orm.mapped_column(sa.String(20), nullable=False, default=JOB_QUEUED, index=True)
    progress: Mapped[int] = orm.mapped_column(sa.Integer, nullable=False, default=0)  # 0..100
    payload: Mapped[dict] = orm.mapped_column(sa.JSON, nullable=False, default=dict)
    result: Mapped[dict | None] = orm.mapped_column(sa.JSON, nullable=True)
    # Safe error summary only — never raw file contents or secrets (SEC-05).
    error: Mapped[dict | None] = orm.mapped_column(sa.JSON, nullable=True)
    correlation_id: Mapped[str | None] = orm.mapped_column(sa.String(100), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), nullable=False)
    started_at: Mapped[dt.datetime | None] = orm.mapped_column(sa.DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = orm.mapped_column(sa.DateTime(timezone=True), nullable=True)
