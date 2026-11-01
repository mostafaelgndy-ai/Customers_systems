"""Identity service layer: registration, authentication, organization context."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import APIError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.domains.identity.models import (
    ROLE_OWNER,
    Membership,
    Organization,
    User,
)
from app.domains.identity.schemas import TokenPair


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    return slug or "org"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def issue_tokens(user: User, organization_id: uuid.UUID | None = None) -> TokenPair:
    org = str(organization_id) if organization_id is not None else None
    return TokenPair(
        access_token=create_access_token(str(user.id), org),
        refresh_token=create_refresh_token(str(user.id)),
        token_type="bearer",
        expires_in=settings.access_token_expires_minutes * 60,
    )


def register(db: Session, *, email: str, password: str, full_name: str | None, organization_name: str) -> User:
    email = normalize_email(email)
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise APIError("An account with this email already exists.", status_code=409, code="CONFLICT")

    slug = _slugify(organization_name)
    slug_taken = db.scalar(select(Organization).where(Organization.slug == slug))
    if slug_taken is not None:
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"

    organization = Organization(name=organization_name, slug=slug, is_active=True)
    user = User(email=email, hashed_password=hash_password(password), full_name=full_name, is_active=True)
    organization.created_by_user_id = user.id
    membership = Membership(user=user, organization=organization, role=ROLE_OWNER, is_active=True)
    db.add_all([organization, user, membership])
    db.flush()
    return user


def authenticate(db: Session, *, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not verify_password(password, user.hashed_password):
        raise APIError("Invalid email or password.", status_code=401, code="UNAUTHENTICATED")
    if not user.is_active:
        raise APIError("This account is deactivated.", status_code=403, code="PERMISSION_DENIED")
    return user


def primary_organization_id(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    """Active membership of a user; None when they have none."""
    return db.scalar(
        select(Membership.organization_id).where(Membership.user_id == user_id, Membership.is_active.is_(True))
    )
