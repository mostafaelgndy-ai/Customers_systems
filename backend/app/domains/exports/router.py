"""CSV export API (API-10/JOB-06).

Endpoints:
  POST /api/v1/exports                   — queue an export for a run's results
  GET  /api/v1/exports/{job_id}/download — stream the produced CSV file
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.db.session import get_db
from app.domains.churn.service import get_model_run
from app.domains.exports import service as export_service
from app.domains.identity.models import Membership
from app.domains.jobs.queue import enqueue
from app.domains.segmentation.service import get_segmentation_run

router = APIRouter(prefix="/exports", tags=["exports"])


class ExportRequest(BaseModel):
    kind: str  # churn_scores | segment_assignments
    run_id: uuid.UUID


@router.post("", status_code=202)
def create_export_endpoint(
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(export_service.EXPORT_ROLES),
    request: ExportRequest,
) -> dict[str, Any]:
    """Queue a CSV export for one succeeded run (API-10).

    The run is scoped-checked up front so unauthorized/cross-tenant
    requests fail fast (404). The Job row records kind + run id; the
    created file path follows from the job identity, not the caller.
    """
    if request.kind not in export_service.EXPORT_KINDS:
        raise APIError(f"kind must be one of {', '.join(export_service.EXPORT_KINDS)}.", 400, code="BAD_REQUEST")
    if request.kind == export_service.CHURN_SCORES:
        get_model_run(db, membership.organization_id, request.run_id)
    else:
        get_segmentation_run(db, membership.organization_id, request.run_id)

    job = enqueue(
        db,
        job_type="export_csv",
        organization_id=membership.organization_id,
        created_by_user_id=membership.user_id,
        payload={"kind": request.kind, "run_id": str(request.run_id)},
    )
    from app.domains.audit.service import record_audit  # SEC-06: exports are sensitivity-tagged

    record_audit(
        db,
        org_id=membership.organization_id,
        user_id=membership.user_id,
        action="export.requested",
        resource_type="job",
        resource_id=job.id,
        details={"kind": request.kind, "run_id": str(request.run_id)},
    )
    db.commit()
    return {
        "job": {
            "id": str(job.id),
            "job_type": job.job_type,
            "status": job.status,
            "payload": job.payload,
        }
    }


@router.get("/{job_id}/download")
def download_export_endpoint(
    job_id: uuid.UUID,
    *,
    db: Session = Depends(get_db),
    membership: Membership = Depends(export_service.EXPORT_ROLES),
) -> FileResponse:
    """Stream the produced CSV (API-10); serves only the recorded path."""
    job, file_path = export_service.resolve_export_job(db, membership.organization_id, job_id)
    from app.domains.audit.service import record_audit  # SEC-06: downloads are audited

    record_audit(
        db,
        org_id=membership.organization_id,
        user_id=membership.user_id,
        action="export.downloaded",
        resource_type="job",
        resource_id=job.id,
        details={"kind": (job.payload or {}).get("kind"), "file": file_path.name},
    )
    db.commit()
    return FileResponse(
        path=file_path,
        media_type="text/csv",
        filename=file_path.name,
    )
