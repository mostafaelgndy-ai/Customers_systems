"""add observation_days to feature_runs

Runs must persist their observation window length so that execution is
reproducible from stored run configuration (FE-08, CHURN-01).

Revision ID: c8d9e0f1a2b3
Revises: b2f3e4d5c6a7
Create Date: 2026-11-01 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8d9e0f1a2b3"
down_revision = "b2f3e4d5c6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feature_runs",
        sa.Column("observation_days", sa.Integer(), nullable=False, server_default="180"),
    )
    op.alter_column("feature_runs", "observation_days", server_default=None)


def downgrade() -> None:
    op.drop_column("feature_runs", "observation_days")
