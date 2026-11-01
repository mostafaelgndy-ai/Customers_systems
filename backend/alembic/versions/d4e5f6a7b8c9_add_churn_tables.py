"""add churn labels, model runs, and scores tables

Revision ID: d4e5f6a7b8c9
Revises: c8d9e0f1a2b3
Create Date: 2026-11-01 11:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "churn_labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.String(length=200), nullable=False),
        sa.Column("cutoff_date", sa.String(length=32), nullable=False),
        sa.Column("observation_window_days", sa.Integer(), nullable=False),
        sa.Column("label_horizon_days", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=20), nullable=False),
        sa.Column("days_since_last_order", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "dataset_version_id",
            "customer_id",
            "cutoff_date",
            "label_horizon_days",
            name="uq_churn_label_org_version_customer_cutoff_horizon",
        ),
    )
    op.create_index(op.f("ix_churn_labels_organization_id"), "churn_labels", ["organization_id"])
    op.create_index(op.f("ix_churn_labels_dataset_version_id"), "churn_labels", ["dataset_version_id"])
    op.create_index(op.f("ix_churn_labels_customer_id"), "churn_labels", ["customer_id"])

    op.create_table(
        "churn_model_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("feature_run_id", sa.Uuid(), nullable=True),
        sa.Column("model_type", sa.String(length=40), nullable=False),
        sa.Column("observation_window_days", sa.Integer(), nullable=False),
        sa.Column("label_horizon_days", sa.Integer(), nullable=False),
        sa.Column("train_cutoff_date", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("artifact_path", sa.String(length=1000), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_churn_model_runs_organization_id"), "churn_model_runs", ["organization_id"])
    op.create_index(
        op.f("ix_churn_model_runs_dataset_version_id"), "churn_model_runs", ["dataset_version_id"]
    )
    op.create_index(op.f("ix_churn_model_runs_feature_run_id"), "churn_model_runs", ["feature_run_id"])

    op.create_table(
        "churn_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("model_run_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.String(length=200), nullable=False),
        sa.Column("churn_probability", sa.Float(), nullable=False),
        sa.Column("risk_band", sa.String(length=20), nullable=False),
        sa.Column("cutoff_date", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "model_run_id",
            "customer_id",
            name="uq_churn_score_org_run_customer",
        ),
    )
    op.create_index(op.f("ix_churn_scores_organization_id"), "churn_scores", ["organization_id"])
    op.create_index(op.f("ix_churn_scores_model_run_id"), "churn_scores", ["model_run_id"])
    op.create_index(op.f("ix_churn_scores_customer_id"), "churn_scores", ["customer_id"])


def downgrade() -> None:
    op.drop_table("churn_scores")
    op.drop_table("churn_model_runs")
    op.drop_index(op.f("ix_churn_labels_customer_id"), table_name="churn_labels")
    op.drop_index(op.f("ix_churn_labels_dataset_version_id"), table_name="churn_labels")
    op.drop_index(op.f("ix_churn_labels_organization_id"), table_name="churn_labels")
    op.drop_table("churn_labels")
