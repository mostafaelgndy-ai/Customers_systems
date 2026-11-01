"""Single job runner shared by inline execution and Celery tasks.

The Job row is the source of truth for status/progress/errors (JOB-07).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import APIError
from app.core.logging import job_id_var
from app.domains.jobs.models import (
    JOB_FAILED,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    Job,
)

logger = logging.getLogger("jobs")

# Registry maps job type -> function taking (db, job) and returning a result dict.
JobHandler = Callable[[Session, Job], dict]
_HANDLERS: dict[str, JobHandler] = {}


def register_handler(job_type: str, handler: JobHandler) -> None:
    _HANDLERS[job_type] = handler


def run_job(db: Session, job: Job) -> dict:
    """Execute a registered handler for this job with status accounting.

    Conservative error storage: messages are truncated summaries only.
    """
    token = job_id_var.set(str(job.id))
    job.status = JOB_RUNNING
    job.started_at = dt.datetime.now(dt.UTC)
    try:
        handler = _HANDLERS.get(job.job_type)
        if handler is None:
            raise RuntimeError(f"No handler registered for job type '{job.job_type}'")
        job.result = handler(db, job) or {}
        job.status = JOB_SUCCEEDED
        job.progress = 100
        return {"id": str(job.id), "status": job.status}
    except APIError as exc:
        job.status = JOB_FAILED
        job.error = {"code": exc.code, "message": exc.message[:500]}
    except Exception as exc:  # noqa: BLE001 - jobs record, never crash callers
        logger.exception("job.failed", extra={"job_id": str(job.id), "type": job.job_type})
        job.status = JOB_FAILED
        job.error = {"code": "JOB_FAILED", "message": str(exc)[:500]}
    finally:
        job.finished_at = dt.datetime.now(dt.UTC)
        job_id_var.reset(token)
    return {"id": str(job.id), "status": job.status}


def run_job_by_id(job_id: uuid.UUID) -> dict:
    """Entry point used by Celery tasks: fresh session for one job."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return {"error": "job row missing"}
        result = run_job(db, job)
        db.commit()
        return result
    finally:
        db.close()


def enqueue(
    db: Session,
    *,
    job_type: str,
    organization_id: uuid.UUID,
    created_by_user_id: uuid.UUID,
    payload: dict,
    correlation_id: str | None = None,
) -> Job:
    """Create the Job row and dispatch it.

    Inline mode executes synchronously within the request (dev/tests);
    queue mode sends it to Celery. The Job row is the source of truth
    either way.
    """
    job = Job(
        organization_id=organization_id,
        job_type=job_type,
        payload=payload,
        correlation_id=correlation_id,
        created_by_user_id=created_by_user_id,
    )
    db.add(job)
    db.flush()

    if settings.inline_jobs:
        run_job(db, job)
    else:
        from app.workers.tasks import dispatch_job

        dispatch_job.delay(str(job.id))
        logger.info("job.queued", extra={"job_id": str(job.id), "type": job_type})
    return job
