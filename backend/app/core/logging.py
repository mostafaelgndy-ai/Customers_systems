"""Structured JSON logging with request/job correlation IDs (ENG-07).

Context vars are set by the request middleware and by job execution so
every log line (and error envelope) carries the correlation id.
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
job_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="-")

REDACTED_FIELDS = {"password", "hashed_password", "token", "refresh_token", "secret_key"}
_REDACTED = "***redacted***"

# Standard LogRecord attributes not echoed into the JSON payload.
_STANDARD_RECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "getMessage",
        "message",
        "asctime",
    }
)


def _redact(value: Any) -> Any:
    """SEC-05: recursively redact secret-looking keys from structured logs."""
    if isinstance(value, dict):
        return {key: (_REDACTED if key in REDACTED_FIELDS else _redact(val)) for key, val in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def new_request_id() -> str:
    return uuid.uuid4().hex


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "job_id": job_id_var.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # SEC-05: extras may carry secrets (e.g. login payloads); redact
        # recursively before serializing.
        extra = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_RECORD_ATTRS}
        payload.update(_redact(extra))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Quieting noisy libs keeps correlation ids readable.
    for name in ("uvicorn.access", "uvicorn.error", "celery.utils.log"):
        logging.getLogger(name).handlers = [handler]
