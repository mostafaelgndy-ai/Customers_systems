"""Request middleware: correlation IDs + access logging (ENG-07)."""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import new_request_id, request_id_var


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, logger_name: str = "api.access") -> None:  # noqa: ANN001
        super().__init__(app)
        self._logger = logging.getLogger(logger_name)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Trust X-Request-ID from trusted callers/proxies; generate otherwise.
        request_id = request.headers.get("X-Request-ID") or new_request_id()
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response


def get_request_id() -> str:
    from app.core.logging import request_id_var  # local import avoids cycles

    return request_id_var.get()
