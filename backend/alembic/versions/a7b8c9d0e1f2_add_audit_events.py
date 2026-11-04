"""Add audit_events table (SEC-06).

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str = "f6a7b8c9d0e1"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_events_organization_id"), "audit_events", ["organization_id"])
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"])


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_events_action"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_organization_id"), table_name="audit_events")
    op.drop_table("audit_events")
