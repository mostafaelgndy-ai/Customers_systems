"""Background job status endpoints (API-11: list + detail)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.db.session import get_db
from app.domains.identity.deps import require_membership
from app.domains.jobs.models import JOB_TYPES, Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    *,
    db: Session = Depends(get_db),
    membership=Depends(require_membership),  # type: ignore[no-untyped-def]
    job_type: str | None = Query(None, pattern="^(" + "|".join(JOB_TYPES) + ")$"),
    status: str | None = Query(None, pattern="^(queued|running|succeeded|failed|cancelled)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict[str, object]:
    """Paginated job list for the organization, newest first (API-11)."""
    query = select(Job).where(Job.organization_id == membership.organization_id)
    if job_type is not None:
        query = query.where(Job.job_type == job_type)
    if status is not None:
        query = query.where(Job.status == status)
    total_query = select(func.count()).select_from(query.subquery())
    total = db.scalar(total_query) or 0
    rows = db.scalars(query.order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "items": [
            {
                "id": str(job.id),
                "type": job.job_type,
                "status": job.status,
                "progress": job.progress,
                "created_at": job.created_at.isoformat() if job.created_at else None,
            }
            for job in rows
        ],
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "total": total,
    }


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
