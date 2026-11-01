"""Identity domain models (PRD §7.1, §10).

Every organization-owned record carries `organization_id` directly or
through a strictly enforced org-scoped parent relation (AUTH-03).
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import ForeignKey, UniqueConstraint, orm
from sqlalchemy.orm import Mapped

from app.db.base import Base, TimestampMixin


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = orm.mapped_column(sa.String(200), nullable=False)
    slug: Mapped[str] = orm.mapped_column(sa.String(200), nullable=False, unique=True)
    is_active: Mapped[bool] = orm.mapped_column(sa.Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[uuid.UUID | None] = orm.mapped_column(sa.Uuid(), nullable=True)

    memberships: Mapped[list[Membership]] = orm.relationship(back_populates="organization")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = orm.mapped_column(sa.String(320), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = orm.mapped_column(sa.String(200), nullable=False)
    full_name: Mapped[str | None] = orm.mapped_column(sa.String(200), nullable=True)
    is_active: Mapped[bool] = orm.mapped_column(sa.Boolean, nullable=False, default=True)

    memberships: Mapped[list[Membership]] = orm.relationship(back_populates="user")


class Membership(Base, TimestampMixin):
    """A user's role within one organization (tenant + role assignment)."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", name="uq_membership"),)

    id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), ForeignKey("users.id"), index=True)
    organization_id: Mapped[uuid.UUID] = orm.mapped_column(sa.Uuid(), ForeignKey("organizations.id"), index=True)
    role: Mapped[str] = orm.mapped_column(sa.String(20), nullable=False, index=True)
    is_active: Mapped[bool] = orm.mapped_column(sa.Boolean, nullable=False, default=True)

    user: Mapped[User] = orm.relationship(back_populates="memberships")
    organization: Mapped[Organization] = orm.relationship(back_populates="memberships")


ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_ANALYST = "analyst"
ROLE_MEMBER = "member"
ALL_ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_ANALYST, ROLE_MEMBER)
# Roles allowed to mutate data (upload/mapping/run creation); PRD AUTH-04.
DATA_MUTATION_ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_ANALYST)
ORG_ADMIN_ROLES = (ROLE_OWNER, ROLE_ADMIN)
