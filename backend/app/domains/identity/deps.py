"""FastAPI dependencies: current user, organization context, role checks.

Tenant isolation starts here (AUTH-03): every protected endpoint resolves
an active membership before touching organization-scoped data.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import jwt
from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.core.security import decode_token
from app.db.session import get_db
from app.domains.identity.models import (
    DATA_MUTATION_ROLES,
    ORG_ADMIN_ROLES,
    Membership,
    User,
)

ORG_HEADER = "X-Organization-ID"


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise APIError(
            "Missing bearer token.",
            status_code=401,
            code="UNAUTHENTICATED",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return auth[7:].strip()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _bearer_token(request)
    try:
        payload = decode_token(token, expected_type="access")
    except jwt.PyJWTError:
        raise APIError(
            "Invalid or expired token.",
            status_code=401,
            code="UNAUTHENTICATED",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    user_id = uuid.UUID(str(payload["sub"]))
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise APIError("Account not found or deactivated.", status_code=401, code="UNAUTHENTICATED")
    return user


def get_membership(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    x_organization_id: str | None = Header(default=None),
) -> Membership:
    """Resolve the acting organization from the header (or the only membership)."""
    stmt = select(Membership).where(Membership.user_id == user.id, Membership.is_active.is_(True))
    if x_organization_id:
        try:
            org_id = uuid.UUID(x_organization_id)
        except ValueError:
            raise APIError("Invalid organization id header.", 400, code="BAD_REQUEST") from None
        membership = db.scalar(stmt.where(Membership.organization_id == org_id))
    else:
        memberships = db.scalars(stmt).all()
        membership = memberships[0] if len(memberships) == 1 else None
        if membership is None:
            raise APIError(
                f"Ambiguous organization context: send the {ORG_HEADER} header.",
                400,
                code="BAD_REQUEST",
            )
    if membership is None:
        raise APIError("You are not a member of that organization.", 403, code="PERMISSION_DENIED")
    return membership


def role_checker(*allowed_roles: str) -> Callable[..., Membership]:
    def dependency(membership: Membership = Depends(get_membership)) -> Membership:
        if membership.role not in allowed_roles:
            raise APIError(
                "Your role does not permit this action.",
                403,
                code="PERMISSION_DENIED",
            )
        return membership

    return dependency


# Reusable dependency factories
require_data_mutation = role_checker(*DATA_MUTATION_ROLES)
require_org_admin = role_checker(*ORG_ADMIN_ROLES)
require_membership = role_checker("owner", "admin", "analyst", "member")


def organization_id_of(membership: Membership) -> uuid.UUID:
    return membership.organization_id
