"""Celery tasks (JOB-01): bridge into the job runner used by inline mode."""

from __future__ import annotations

import uuid

from app.workers.celery_app import app


@app.task(name="dispatch_job", bind=True, max_retries=3, default_retry_delay=10)
def dispatch_job(self, job_id: str) -> dict:  # type: ignore[no-untyped-def]
    """Dispatch the job row to its handler; manual ack, idempotent execution.

    Retries are safe (JOB-08): handlers skip finished versions and the
    runner only executes a handler for jobs re-claimed from the queue.
    """
    # The Celery process does not run the API lifespan, so handler
    # registration is done explicitly here (JOB-01).
    from app.domains.churn import jobs as churn_jobs
    from app.domains.datasets import jobs as dataset_jobs
    from app.domains.exports import jobs as export_jobs
    from app.domains.features import jobs as feature_jobs
    from app.domains.segmentation import jobs as segmentation_jobs

    for module in (dataset_jobs, feature_jobs, churn_jobs, segmentation_jobs, export_jobs):
        module.register()

    from app.domains.jobs.queue import run_job_by_id

    try:
        return run_job_by_id(uuid.UUID(job_id))
    except ValueError as exc:  # poison payload — do not retry
        raise RuntimeError(f"Bad job id payload: {job_id}") from exc
