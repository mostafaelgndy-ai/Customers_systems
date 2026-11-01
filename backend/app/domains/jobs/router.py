"""Background job status endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.db.session import get_db
from app.domains.identity.deps import require_membership
from app.domains.jobs.models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership=Depends(require_membership),  # type: ignore[no-untyped-def]
) -> dict:
    job = db.get(Job, job_id)
    if job is None or job.organization_id != membership.organization_id:
        raise APIError("Job not found.", 404, code="NOT_FOUND")
    return {
        "id": str(job.id),
        "type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "correlation_id": job.correlation_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
