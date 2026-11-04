"""Export job handler (JOB-06)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.domains.exports.service import execute_export
from app.domains.jobs.queue import register_handler

EXPORT_CSV = "export_csv"


def handle_export_csv(db: Session, job: Any) -> dict[str, Any]:
    return execute_export(
        db,
        org_id=job.organization_id,
        job_id=job.id,
        kind=str(job.payload["kind"]),
        run_id=uuid.UUID(str(job.payload["run_id"])),
    )


def register() -> None:
    register_handler(EXPORT_CSV, handle_export_csv)


register()
