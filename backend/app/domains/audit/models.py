"""Audit event model (SEC-06): one row per sensitive action."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import orm
from sqlalchemy.orm import Mapped

from app.db.base import Base, TimestampMixin


class AuditEvent(Base, TimestampMixin):
    """Append-only audit trail of sensitive actions.

    Never editable or deletable through the API; org-scoped reads only.
    """

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), index=True, nullable=False)
    user_id: Mapped[uuid.UUID | None] = orm.mapped_column(sa.Uuid(), nullable=True)
    action: Mapped[str] = orm.mapped_column(sa.String(100), nullable=False, index=True)
    resource_type: Mapped[str] = orm.mapped_column(sa.String(50), nullable=False)
    resource_id: Mapped[str] = orm.mapped_column(sa.String(64), nullable=False)
    # PII-free structured context only (SEC-05 interplay); secrets are
    # redacted at logging time, never stored here.
    details: Mapped[dict | None] = orm.mapped_column(sa.JSON, nullable=True)
