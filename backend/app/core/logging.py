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
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Quieting noisy libs keeps correlation ids readable.
    for name in ("uvicorn.access", "uvicorn.error", "celery.utils.log"):
        logging.getLogger(name).handlers = [handler]
