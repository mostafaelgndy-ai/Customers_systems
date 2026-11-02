"""Add segmentation runs and assignments (Phase 5, SEG-09).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str = "e5f6a7b8c9d0"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "segmentation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("observation_window_days", sa.Integer(), nullable=False),
        sa.Column("random_seed", sa.Integer(), nullable=False),
        sa.Column("selected_k", sa.Integer(), nullable=True),
        sa.Column("cutoff_date", sa.String(length=32), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("profiles", sa.JSON(), nullable=True),
        sa.Column("candidate_report", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("artifact_path", sa.String(length=1000), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_segmentation_runs_organization_id"), "segmentation_runs", ["organization_id"])
    op.create_index(op.f("ix_segmentation_runs_dataset_version_id"), "segmentation_runs", ["dataset_version_id"])

    op.create_table(
        "segment_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("segmentation_run_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.String(length=200), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("segment_label", sa.String(length=200), nullable=False),
        sa.Column("cutoff_date", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "segmentation_run_id",
            "customer_id",
            name="uq_segment_assignment_org_run_customer",
        ),
    )
    op.create_index(op.f("ix_segment_assignments_organization_id"), "segment_assignments", ["organization_id"])
    op.create_index(op.f("ix_segment_assignments_segmentation_run_id"), "segment_assignments", ["segmentation_run_id"])
    op.create_index(op.f("ix_segment_assignments_customer_id"), "segment_assignments", ["customer_id"])


def downgrade() -> None:
    op.drop_table("segment_assignments")
    op.drop_table("segmentation_runs")
