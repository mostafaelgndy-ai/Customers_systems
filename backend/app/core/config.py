"""Application settings loaded from environment variables / `.env` (ENG-03)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expires_minutes: int = 30
    refresh_token_expires_days: int = 7
    bcrypt_rounds: int = 12

    # --- uploads ---
    upload_dir: Path = Path("data/uploads")
    max_upload_mb: int = 50
    allowed_upload_extensions: tuple[str, ...] = (".csv", ".txt")

    # --- trained model artifacts (CHURN-12) ---
    models_dir: Path = Path("data/models")

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

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
