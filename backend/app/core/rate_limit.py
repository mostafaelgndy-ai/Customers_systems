"""Simple per-IP rate limiting (SEC-04).

Fixed-window counters keyed by (client IP, bucket). Buckets are matched
by path regex so authentication, uploads, and expensive run-creation
endpoints can have separate limits. Disabled by default (tests/dev) and
gated by `Settings.rate_limit_enabled`; limits are configuration, not
hardcoded business truth.

The middleware short-circuits with the reserved RATE_LIMITED code
("429") from core/errors before any handler or auth runs.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings


# (bucket, path regex). First match wins; ordered from most specific.
def default_buckets() -> list[tuple[str, str]]:
    return [
        ("expensive", r"^/api/v1/(churn/(labels|scores)|features|segmentation/runs|exports)(/.*)?$"),
        ("uploads", r"^/api/v1/datasets/.*/files$"),
        ("auth", r"^/api/v1/auth/.*$"),
    ]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window limiter: rejects requests over the bucket's per-minute quota."""

    def __init__(self, app: Callable[..., Response], *, buckets: list[tuple[str, str]] | None = None) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        compiled = buckets or default_buckets()
        self._buckets: tuple[tuple[str, re.Pattern[str], int], ...] = tuple(
            (bucket, re.compile(pattern), limit) for bucket, pattern in compiled for limit in (self._limit_for(bucket),)
        )
        # key -> window start -> count (single-process store; multi-replica
        # deployments need the shared store, which is out of pilot scope).
        self._counters: dict[tuple[str, str, int], int] = {}

    @staticmethod
    def _limit_for(bucket: str) -> int:
        mapping = {
            "auth": settings.rate_limit_auth_per_minute,
            "uploads": settings.rate_limit_uploads_per_minute,
            "expensive": settings.rate_limit_expensive_per_minute,
        }
        return int(mapping.get(bucket, settings.rate_limit_default_per_minute))

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if not settings.rate_limit_enabled:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path
        for bucket, pattern, limit in self._buckets:
            if pattern.match(path):
                key = (client_ip, bucket, int(time.time() // 60))
                count = self._counters.get(key, 0) + 1
                self._counters[key] = count
                # garbage collection of stale windows
                current_window = int(time.time() // 60)
                self._counters = {k: v for k, v in self._counters.items() if k[2] >= current_window}
                if count > limit:
                    return JSONResponse(
                        status_code=429,
                        content={"error": {"code": "RATE_LIMITED", "message": "Too many requests. Slow down."}},
                        headers={"Retry-After": "60"},
                    )
                break  # only the first matching bucket counts
        return await call_next(request)
