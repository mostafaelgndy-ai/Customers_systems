"""Audit trail API (SEC-06).

GET /api/v1/audit — org-scoped, append-only audit trail, newest first.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domains.audit import service as audit_service
from app.domains.identity.deps import role_checker
from app.domains.identity.models import Membership

router = APIRouter(prefix="/audit", tags=["audit"])

_OWNER_ROLES = role_checker("owner")  # audit trail is owner-only


@router.get("")
def list_audit_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_OWNER_ROLES),
    action: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """The calling organization's audit trail (SEC-06)."""
    items, total = audit_service.list_audit_events(
        db, membership.organization_id, action=action, page=page, page_size=page_size
    )
    return {
        "items": [
            {
                "action": event.action,
                "resource_type": event.resource_type,
                "resource_id": event.resource_id,
                "user_id": str(event.user_id) if event.user_id else None,
                "details": event.details,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in items
        ],
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "total": total,
    }
