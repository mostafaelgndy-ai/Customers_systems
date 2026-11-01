"""Feature engineering models (FE-07, FE-10).

Feature snapshots are immutable per (organization, dataset version,
cutoff, feature version). Each snapshot stores the computed feature
frame as JSON plus metadata for traceability and leakage auditing.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import ForeignKey, orm
from sqlalchemy.orm import Mapped

from app.db.base import Base, TimestampMixin
from app.domains.features.pipeline import DEFAULT_OBSERVATION_DAYS

if TYPE_CHECKING:
    from app.domains.datasets.models import DatasetVersion

# Feature version — bump when the feature schema or computation changes.
FEATURE_VERSION = "1.0.0"

# Statuses for feature runs.
FEATURE_RUN_QUEUED = "queued"
FEATURE_RUN_RUNNING = "running"
FEATURE_RUN_SUCCEEDED = "succeeded"
FEATURE_RUN_FAILED = "failed"
FEATURE_RUN_CANCELLED = "cancelled"


class FeatureRun(Base, TimestampMixin):
    """A feature computation run linked to a dataset version.

    One run produces one FeatureSnapshot. Runs are organization-scoped
    and traceable back to the dataset version and cutoff used.
    """

    __tablename__ = "feature_runs"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    dataset_version_id: Mapped[uuid.UUID] = orm.mapped_column(
        sa.Uuid(), ForeignKey("dataset_versions.id"), index=True, nullable=False
    )
    cutoff_date: Mapped[str] = orm.mapped_column(sa.String(32), nullable=False)  # ISO date
    observation_days: Mapped[int] = orm.mapped_column(sa.Integer, nullable=False, default=DEFAULT_OBSERVATION_DAYS)
    feature_version: Mapped[str] = orm.mapped_column(sa.String(32), nullable=False, default=FEATURE_VERSION)
    status: Mapped[str] = orm.mapped_column(sa.String(20), nullable=False, default=FEATURE_RUN_QUEUED)
    row_count: Mapped[int | None] = orm.mapped_column(sa.Integer, nullable=True)
    error: Mapped[str | None] = orm.mapped_column(sa.Text(), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), nullable=False)

    dataset_version: Mapped[DatasetVersion] = orm.relationship(foreign_keys=[dataset_version_id])
    snapshots: Mapped[list[FeatureSnapshot]] = orm.relationship(  # type: ignore[assignment]
        back_populates="feature_run",
        foreign_keys="FeatureSnapshot.feature_run_id",
        cascade="all, delete-orphan",
    )


class FeatureSnapshot(Base, TimestampMixin):
    """Immutable persisted feature values for one run.

    The `features` column holds a list of customer feature dicts, each
    keyed by customer_id. Stored as JSON for simplicity in Phase 3;
    may migrate to a relational feature store later.
    """

    __tablename__ = "feature_snapshots"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    feature_run_id: Mapped[uuid.UUID] = orm.mapped_column(
        sa.Uuid(), ForeignKey("feature_runs.id"), index=True, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    dataset_version_id: Mapped[uuid.UUID] = orm.mapped_column(
        sa.Uuid(), ForeignKey("dataset_versions.id"), index=True, nullable=False
    )
    cutoff_date: Mapped[str] = orm.mapped_column(sa.String(32), nullable=False)
    feature_version: Mapped[str] = orm.mapped_column(sa.String(32), nullable=False, default=FEATURE_VERSION)
    features: Mapped[list[dict]] = orm.mapped_column(sa.JSON, nullable=False, default=list)
    created_by_user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), nullable=False)

    feature_run: Mapped[FeatureRun] = orm.relationship(
        back_populates="snapshots",
        foreign_keys=[feature_run_id],
    )
