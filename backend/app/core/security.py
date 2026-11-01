"""Password hashing and JWT issuing/verification.

Access tokens are short-lived JWTs. Refresh tokens are JWTs with
`{"type": "refresh"}` and rotate on every refresh (stateless for the MVP;
DB-backed revocation is a Phase 10 hardening task).
"""

from __future__ import annotations

import datetime as dt
import uuid

import bcrypt
import jwt

from app.core.config import settings

# --- passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


# --- JWTs --------------------------------------------------------------------


def _create_token(
    *,
    subject: str,
    token_type: str,
    lifetime: dt.timedelta,
    extra: dict[str, str] | None = None,
) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
        "jti": uuid.uuid4().hex,
    }
    if extra:
        payload.update({key: str(value) for key, value in extra.items()})
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str, organization_id: str | None = None) -> str:
    exp = dt.timedelta(minutes=settings.access_token_expires_minutes)
    extra = {"org": organization_id} if organization_id else None
    return _create_token(subject=user_id, token_type="access", lifetime=exp, extra=extra)


def create_refresh_token(user_id: str) -> str:
    exp = dt.timedelta(days=settings.refresh_token_expires_days)
    return _create_token(subject=user_id, token_type="refresh", lifetime=exp)


def decode_token(token: str, expected_type: str) -> dict[str, object]:
    """Decode and validate; raises `jwt.PyJWTError` on any failure."""
    payload: dict[str, object] = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return payload
