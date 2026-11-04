"""CustomerIQ API entrypoint (FastAPI factory).

Mounts identity, datasets, features, churn, and jobs domains; wires
structured logging, correlation middleware, and the error envelope.
"""

from __future__ import annotations

import contextlib
import logging

import sqlalchemy as sa
from fastapi import FastAPI

from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestLoggingMiddleware
from app.db.session import engine
from app.domains.audit.router import router as audit_router
from app.domains.churn.router import router as churn_router
from app.domains.customers.router import router as customers_router
from app.domains.datasets.router import router as datasets_router
from app.domains.exports.router import router as exports_router
from app.domains.features.router import router as features_router
from app.domains.identity.router import router as identity_router
from app.domains.jobs.router import router as jobs_router
from app.domains.segmentation.router import router as segmentation_router

configure_logging(settings.log_level)
logger = logging.getLogger("api")


def _include_routers(app: FastAPI) -> None:
    prefix = "/api/v1"
    app.include_router(identity_router, prefix=prefix)
    app.include_router(datasets_router, prefix=prefix)
    app.include_router(features_router, prefix=prefix)
    app.include_router(churn_router, prefix=prefix)
    app.include_router(customers_router, prefix=prefix)
    app.include_router(segmentation_router, prefix=prefix)
    app.include_router(exports_router, prefix=prefix)
    app.include_router(audit_router, prefix=prefix)
    app.include_router(jobs_router, prefix=prefix)


def _health_router(app: FastAPI) -> None:
    @app.get("/api/v1/health", tags=["health"])
    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        # Liveness only: must never touch dependencies.
        return {"status": "ok", "environment": settings.environment}

    @app.get("/api/v1/health/readiness", tags=["health"])
    def readiness() -> dict:
        checks: dict[str, str] = {}
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:  # noqa: BLE001 - readiness aggregates, never leaks internals
            checks["database"] = "unavailable"
        try:
            import redis  # deferred import: not needed at process start

            client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
            client.ping()
            checks["redis"] = "ok"
        except Exception:  # noqa: BLE001
            checks["redis"] = "unavailable"
        ok = all(v == "ok" for v in checks.values())
        return {"status": "ok" if ok else "degraded", "checks": checks}


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    from app.domains.churn import jobs as churn_jobs
    from app.domains.datasets import jobs as dataset_jobs
    from app.domains.exports import jobs as export_jobs
    from app.domains.features import jobs as feature_jobs
    from app.domains.segmentation import jobs as segmentation_jobs

    dataset_jobs.register()  # ensure handlers registered in every entrypoint
    feature_jobs.register()  # ensure feature job handlers registered
    churn_jobs.register()  # ensure churn training/scoring handlers registered
    segmentation_jobs.register()  # ensure segmentation handler registered
    export_jobs.register()  # ensure export_csv handler registered
    logger.info("startup completed")
    yield
    logger.info("shutdown completed")


def create_app() -> FastAPI:
    app = FastAPI(
        title="CustomerIQ API",
        version="0.1.0",
        description=(
            "B2B customer intelligence: CSV ingestion & validation, churn "
            "prediction, and segmentation. See docs/ for contracts."
        ),
        lifespan=_lifespan,
    )

    _health_router(app)
    app.add_middleware(RequestLoggingMiddleware)
    if settings.rate_limit_enabled:
        from app.core.rate_limit import RateLimitMiddleware

        app.add_middleware(RateLimitMiddleware)  # type: ignore[arg-type]
    _include_routers(app)
    register_exception_handlers(app)
    return app


app = create_app()
