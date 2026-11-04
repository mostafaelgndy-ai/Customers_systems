"""Audit recording service (SEC-06).

`record_audit` participates in the caller's transaction (adds, never
commits) so the event and the action it describes are persisted
atomically. Endpoints with no surrounding transaction commit explicitly.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.audit.models import AuditEvent


def record_audit(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: str | uuid.UUID,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append one audit event row (SEC-06). Caller commits."""
    event = AuditEvent(
        organization_id=org_id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        details=details or {},
    )
    db.add(event)
    db.flush()
    return event


def list_audit_events(
    db: Session,
    org_id: uuid.UUID,
    *,
    action: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AuditEvent], int]:
    """Org-scoped audit trail, newest first."""
    query = select(AuditEvent).where(AuditEvent.organization_id == org_id)
    if action is not None:
        query = query.where(AuditEvent.action == action)
    rows = list(db.scalars(query.order_by(AuditEvent.created_at.desc())).all())
    return rows[(page - 1) * page_size : page * page_size], len(rows)
