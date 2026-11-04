"""Export services (API-10/JOB-06).

`execute_export` turns a succeeded run's rows into a CSV file under
`Settings.exports_dir`/<org>/<job>.csv and returns metadata for the Job
result. `download_export` resolves a succeeded export job's file safely.
"""

from __future__ import annotations

import csv
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import APIError
from app.domains.churn.models import ChurnScore
from app.domains.identity.deps import role_checker
from app.domains.jobs.models import JOB_SUCCEEDED, Job
from app.domains.segmentation.models import SegmentAssignment

EXPORT_ROLES = role_checker("owner", "admin", "analyst")

CHURN_SCORES = "churn_scores"
SEGMENT_ASSIGNMENTS = "segment_assignments"
EXPORT_KINDS = (CHURN_SCORES, SEGMENT_ASSIGNMENTS)


def export_path(job_id: uuid.UUID, org_id: uuid.UUID) -> Path:
    """Deterministic, caller-independent file location for one export job."""
    return Path(settings.exports_dir) / str(org_id) / f"{job_id}.csv"


def execute_export(
    db: Session,
    *,
    org_id: uuid.UUID,
    job_id: uuid.UUID,
    kind: str,
    run_id: uuid.UUID,
) -> dict[str, Any]:
    """Write the CSV for one export job (JOB-06) and return job-result data.

    Rows are selected with the organization filter (API-10): a job can
    only ever export the creating organization's data.
    """
    if kind == CHURN_SCORES:
        score_rows: list[ChurnScore] = list(
            db.scalars(
                select(ChurnScore)
                .where(ChurnScore.organization_id == org_id, ChurnScore.model_run_id == run_id)
                .order_by(ChurnScore.customer_id)
            )
        )
        header = [
            "customer_id",
            "churn_probability",
            "risk_band",
            "cutoff_date",
            "model_run_id",
            "generated_at",
        ]
        records = [
            [
                row.customer_id,
                row.churn_probability,
                row.risk_band,
                row.cutoff_date,
                str(row.model_run_id),
                row.created_at.isoformat() if row.created_at else "",
            ]
            for row in score_rows
        ]
    elif kind == SEGMENT_ASSIGNMENTS:
        assignment_rows: list[SegmentAssignment] = list(
            db.scalars(
                select(SegmentAssignment)
                .where(
                    SegmentAssignment.organization_id == org_id,
                    SegmentAssignment.segmentation_run_id == run_id,
                )
                .order_by(SegmentAssignment.customer_id)
            )
        )
        header = [
            "customer_id",
            "cluster_id",
            "segment_label",
            "cutoff_date",
            "segmentation_run_id",
            "generated_at",
        ]
        records = [
            [
                row.customer_id,
                row.cluster_id,
                row.segment_label,
                row.cutoff_date,
                str(row.segmentation_run_id),
                row.created_at.isoformat() if row.created_at else "",
            ]
            for row in assignment_rows
        ]
    else:
        raise APIError(f"Unknown export kind '{kind}'.", 400, code="BAD_REQUEST")

    path = export_path(job_id, org_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(records)

    return {
        "kind": kind,
        "run_id": str(run_id),
        "file_path": str(path),
        "row_count": len(records),
    }


def resolve_export_job(db: Session, org_id: uuid.UUID, job_id: uuid.UUID) -> tuple[Job, Path]:
    """Load a scoped export job and its produced file (404-safe).

    Only a `succeeded` export job owned by the organization resolves; the
    file path always comes from the stored result, never from the caller.
    """
    job = db.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.organization_id == org_id,
            Job.job_type == "export_csv",
        )
    )
    if job is None:
        raise APIError("Export not found.", 404, code="NOT_FOUND")
    if job.status != JOB_SUCCEEDED:
        raise APIError(
            f"Export job status is '{job.status}'; download requires 'succeeded'.",
            409,
            code="CONFLICT",
        )
    file_path = Path((job.result or {}).get("file_path", ""))
    if not file_path or not file_path.is_file():
        raise APIError("Export file is missing.", 404, code="NOT_FOUND")
    # Defense in depth: the recorded path must sit under the exports dir.
    if settings.exports_dir.resolve() not in file_path.resolve().parents:
        raise APIError("Export file is not available.", 404, code="NOT_FOUND")
    return job, file_path
