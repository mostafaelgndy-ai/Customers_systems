"""Churn prediction models (CHURN-01 … CHURN-17).

Stores churn labels, model training runs, model artifacts, and customer
scores. All tables are organization-scoped and traceable to dataset
versions and feature snapshots.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import orm
from sqlalchemy.orm import Mapped

from app.db.base import Base, TimestampMixin

# Churn label values.
CHURN_LABEL_CHURNED = "churned"
CHURN_LABEL_ACTIVE = "active"
CHURN_LABEL_INELIGIBLE = "ineligible"

# Model run statuses.
MODEL_RUN_QUEUED = "queued"
MODEL_RUN_RUNNING = "running"
MODEL_RUN_SUCCEEDED = "succeeded"
MODEL_RUN_FAILED = "failed"

# Model types.
MODEL_TYPE_LOGISTIC_REGRESSION = "logistic_regression"
MODEL_TYPE_RANDOM_FOREST = "random_forest"

# Risk band thresholds (CHURN-14 defaults).
RISK_BAND_LOW_MAX = 0.30
RISK_BAND_MEDIUM_MAX = 0.69


class ChurnLabel(Base, TimestampMixin):
    """Churn label for one customer at one cutoff date.

    Labels are derived from future outcome data relative to the cutoff:
    a customer is "churned" if they have no eligible orders in the
    label horizon window after the cutoff (CHURN-03).
    """

    __tablename__ = "churn_labels"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    dataset_version_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    feature_run_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    customer_id: Mapped[str] = orm.mapped_column(sa.String(200), nullable=False, index=True)
    cutoff_date: Mapped[str] = orm.mapped_column(sa.String(32), nullable=False)
    label_horizon_days: Mapped[int] = orm.mapped_column(sa.Integer, nullable=False)
    label: Mapped[str] = orm.mapped_column(sa.String(20), nullable=False)  # churned | active | ineligible
    days_since_last_order: Mapped[int | None] = orm.mapped_column(sa.Integer, nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id",
            "dataset_version_id",
            "customer_id",
            "cutoff_date",
            name="uq_churn_label_org_version_customer_cutoff",
        ),
    )


class ChurnModelRun(Base, TimestampMixin):
    """A model training run.

    Tracks hyperparameters, dataset version, feature version, split
    configuration, and final metrics (CHURN-05…12).
    """

    __tablename__ = "churn_model_runs"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    dataset_version_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    feature_run_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    model_type: Mapped[str] = orm.mapped_column(sa.String(40), nullable=False)  # logistic_regression | random_forest
    observation_window_days: Mapped[int] = orm.mapped_column(sa.Integer, nullable=False)
    label_horizon_days: Mapped[int] = orm.mapped_column(sa.Integer, nullable=False)
    train_cutoff_date: Mapped[str] = orm.mapped_column(sa.String(32), nullable=False)
    status: Mapped[str] = orm.mapped_column(sa.String(20), nullable=False, default=MODEL_RUN_QUEUED)
    metrics: Mapped[dict | None] = orm.mapped_column(sa.JSON, nullable=True)
    artifact_path: Mapped[str | None] = orm.mapped_column(sa.String(1000), nullable=True)
    error: Mapped[str | None] = orm.mapped_column(sa.Text(), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), nullable=False)


class ChurnScore(Base, TimestampMixin):
    """Churn probability score for one customer from one model run.

    Each score references the model run that produced it and the feature
    snapshot used as input (CHURN-13, CHURN-17).
    """

    __tablename__ = "churn_scores"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    model_run_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    customer_id: Mapped[str] = orm.mapped_column(sa.String(200), nullable=False, index=True)
    churn_probability: Mapped[float] = orm.mapped_column(sa.Float, nullable=False)
    risk_band: Mapped[str] = orm.mapped_column(sa.String(20), nullable=False)  # low | medium | high
    cutoff_date: Mapped[str] = orm.mapped_column(sa.String(32), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id",
            "model_run_id",
            "customer_id",
            name="uq_churn_score_org_run_customer",
        ),
    )
