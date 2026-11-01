"""API error envelope (ENG-06) and FastAPI exception handlers.

Every error response uses the shape documented in docs/api-conventions.md.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.middleware import get_request_id

# Stable error codes (see docs/api-conventions.md §1)
CODE_BY_STATUS: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
}
STATUS_BY_CODE = {v: k for k, v in CODE_BY_STATUS.items()}


class APIError(Exception):
    """Domain error carrying a stable code and safe, client-facing message."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        *,
        code: str | None = None,
        field_errors: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or CODE_BY_STATUS.get(status_code, "BAD_REQUEST")
        self.status_code = status_code
        self.field_errors = field_errors
        self.headers = headers


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    field_errors: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "field_errors": field_errors or [],
            "request_id": get_request_id(),
        }
    }
    return JSONResponse(body, status_code=status_code, headers=headers)


def register_exception_handlers(app: Any) -> None:
    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            field_errors=exc.field_errors,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        field_errors = [
            {
                "field": ".".join(str(part) for part in err.get("loc", [])),
                "message": str(err.get("msg", "invalid value")),
            }
            for err in exc.errors()
        ]
        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_ERROR",
            message="Request body failed validation.",
            field_errors=field_errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _starlette_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            code=CODE_BY_STATUS.get(exc.status_code, "BAD_REQUEST"),
            message=str(exc.detail) if exc.status_code != 500 else "Internal server error.",
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Deliberately opaque: details go to structured logs only (SEC-05).
        logging.getLogger("api").exception("unhandled_error", extra={"path": request.url.path})
        return error_response(
            status_code=500,
            code="INTERNAL_ERROR",
            message="An unexpected error occurred.",
        )
