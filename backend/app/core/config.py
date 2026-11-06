"""Application settings loaded from environment variables / `.env` (ENG-03)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# The development-only default secret (TEST-17): prod startup must reject it.
DEV_SECRET = "dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- application ---
    environment: str = "dev"  # dev | staging | prod
    log_level: str = "INFO"

    # --- database & cache ---
    database_url: str = "sqlite:///./dev.db"
    redis_url: str = "redis://localhost:6379/0"

    # --- background jobs ---
    # dev/tests run inline (synchronous); staging/prod must use Celery workers.
    inline_jobs: bool = True

    # --- security ---
    secret_key: str = DEV_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expires_minutes: int = 30
    refresh_token_expires_days: int = 7
    bcrypt_rounds: int = 12

    # --- CORS (TEST-18): the dashboard is browser-side, so the API must
    # allow the frontend origin. Comma-separated via CORS_ORIGINS. Empty
    # disables CORS entirely (for API-only deployments).
    # NoDecode: plain strings must not be JSON-decoded by pydantic-settings;
    # `_parse_cors_origins` below splits them instead.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        """Accept 'http://a,http://b' as well as JSON ('[...]' required otherwise)."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    # --- uploads ---
    upload_dir: Path = Path("data/uploads")
    max_upload_mb: int = 50
    allowed_upload_extensions: tuple[str, ...] = (".csv", ".txt")

    # --- trained model artifacts (CHURN-12) ---
    models_dir: Path = Path("data/models")

    # --- CSV exports (API-10/JOB-06) ---
    exports_dir: Path = Path("data/exports")

    # --- rate limiting (SEC-04): disabled by default; limits are config ---
    rate_limit_enabled: bool = False
    rate_limit_auth_per_minute: int = 30
    rate_limit_uploads_per_minute: int = 10
    rate_limit_expensive_per_minute: int = 10
    rate_limit_default_per_minute: int = 60

    # --- risk bands (CHURN-14): configuration, not hardcoded business truth ---
    risk_band_low_max: float = 0.30  # probability < low_max  → "low"
    risk_band_high_min: float = 0.70  # probability >= high_min → "high"

    # --- validation engine defaults (Phase 3/4 pipelines may relax these) ---
    exclude_order_statuses: tuple[str, ...] = (
        "pending",
        "processing",
        "shipped",
        "cancelled",
        "refunded",
        "returned",
    )

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    @model_validator(mode="after")
    def _reject_insecure_prod_settings(self) -> Settings:
        """Fail fast when prod runs with development secrets (TEST-17/SEC-06)."""
        if self.is_prod:
            if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
                raise ValueError(
                    "SECRET_KEY must be a strong random value (>= 32 chars) in production; "
                    "the development default is rejected."
                )
            if self.database_url.startswith("sqlite"):
                raise ValueError("SQLite is not a production database; set DATABASE_URL.")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
