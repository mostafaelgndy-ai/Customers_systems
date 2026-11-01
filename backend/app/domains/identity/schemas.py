"""Identity request/response schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class UserSchema(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    is_active: bool


class OrganizationSchema(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str | None = None


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str | None = None
    organization_name: str = Field(min_length=2, max_length=200)
    # MVP: self-service registration creates the organization with the
    # registrant as owner. External invitations are AUTH-05 (P1).


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    user: UserSchema
    organizations: list[OrganizationSchema]
