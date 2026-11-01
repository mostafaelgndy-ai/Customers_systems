"""Authentication endpoints (API-01)."""

from __future__ import annotations

import uuid

import jwt as pyjwt
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.core.security import decode_token
from app.db.session import get_db
from app.domains.identity import service
from app.domains.identity.deps import get_current_user
from app.domains.identity.models import Membership, Organization, User
from app.domains.identity.schemas import (
    LoginRequest,
    MeResponse,
    OrganizationSchema,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserSchema,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenPair, status_code=201)
def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
) -> TokenPair:
    """Create the user and their organization; the registrant becomes owner."""
    user = service.register(
        db,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        organization_name=body.organization_name,
    )
    org_id = service.primary_organization_id(db, user.id)
    return service.issue_tokens(user, org_id)


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenPair:
    user = service.authenticate(db, email=body.email, password=body.password)
    org_id = service.primary_organization_id(db, user.id)
    return service.issue_tokens(user, org_id)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
    except pyjwt.PyJWTError:
        raise APIError("Invalid or expired refresh token.", 401, code="UNAUTHENTICATED") from None
    user = db.get(User, uuid.UUID(str(payload["sub"])))
    if user is None or not user.is_active:
        raise APIError("Account not found or deactivated.", 401, code="UNAUTHENTICATED")
    org_id = service.primary_organization_id(db, user.id)
    return service.issue_tokens(user, org_id)


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MeResponse:
    rows = db.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(Membership.user_id == user.id, Membership.is_active.is_(True))
    ).all()
    orgs = [OrganizationSchema(id=org.id, name=org.name, slug=org.slug, role=role) for org, role in rows]
    return MeResponse(
        user=UserSchema(id=user.id, email=user.email, full_name=user.full_name, is_active=user.is_active),
        organizations=orgs,
    )
