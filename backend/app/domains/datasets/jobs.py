"""The `validate_dataset` job handler (JOB-02, JOB-07..09)."""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domains.datasets import storage
from app.domains.datasets.models import (
    VERSION_FAILED,
    VERSION_INVALID,
    VERSION_VALID,
    VERSION_VALIDATING,
    DatasetFile,
    DatasetVersion,
    DatasetVersionFile,
)
from app.domains.datasets.validation import run_validation
from app.domains.jobs.models import VALIDATE_DATASET


def handle_validate_dataset(db: Session, job) -> dict:  # type: ignore[no-untyped-def]
    """Validate one dataset version and finalize its status.

    Idempotent (JOB-08): if the version already left `validating` (e.g. a
    retry raced), the job exits without writing anything, so retries can
    never create duplicate reports or corrupt status (DATA-13).
    A failed run can never overwrite a previous successful version (PRD §8.2):
    every version is its own row with its own final state.
    """
    version_id = uuid.UUID(str(job.payload["version_id"]))
    version = db.get(DatasetVersion, version_id)
    if version is None:
        raise APIError("Dataset version not found.", 404, code="NOT_FOUND")
    if version.status != VERSION_VALIDATING:
        return {"skipped": True, "reason": f"version already {version.status}"}

    links = db.scalars(select(DatasetVersionFile).where(DatasetVersionFile.dataset_version_id == version.id)).all()
    file_ids = {link.file_kind: link.dataset_file_id for link in links}

    files: dict[str, DatasetFile] = {}
    for kind, file_id in file_ids.items():
        file = db.get(DatasetFile, file_id)
        if file is None or file.organization_id != version.organization_id:
            raise APIError("Linked file not found for this version.", 404, code="NOT_FOUND")
        files[kind] = file

    try:
        report = run_validation(
            storage.read_file_bytes(files["customers"].storage_path),
            storage.read_file_bytes(files["orders"].storage_path),
            cast(dict[str, str] | None, files["customers"].mapping),
            cast(dict[str, str], files["orders"].mapping),
        )
    except APIError:
        version.status = VERSION_FAILED
        raise
    except Exception as exc:  # noqa: BLE001 - engine errors land in version, not logs alone
        version.status = VERSION_FAILED
        version.error_summary = {
            "code": "VALIDATION_ENGINE_ERROR",
            "message": str(exc)[:500],
        }
        raise

    version.report = report
    job.progress = 100
    version.status = VERSION_VALID if report["status"] == "valid" else VERSION_INVALID
    return {"status": version.status, "issue_counts": report["issue_counts"]}


def register() -> None:
    from app.domains.jobs.queue import register_handler

    register_handler(VALIDATE_DATASET, handle_validate_dataset)


register()
