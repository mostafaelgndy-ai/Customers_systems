"""Dataset API surface (API-03, API-04, DATA-05 templates).

Workflow:
  1. `POST /datasets` create container
  2. `POST /datasets/{id}/files?kind=customers|orders` upload raw CSV (size + content checks)
  3. `GET .../files/{fid}/suggest-mapping`, `PATCH .../files/{fid}` set mapping
  4. `POST /datasets/{id}/versions` bind both files into an immutable version
  5. `GET .../versions/{vid}` validation report; `POST .../cancel`, `POST .../retry`
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import APIError
from app.core.logging import request_id_var
from app.core.pagination import PageParams, build_page
from app.db.session import get_db
from app.domains.datasets import service as ds_service
from app.domains.datasets import storage
from app.domains.datasets.eda import compute_eda
from app.domains.datasets.mapping import CANONICAL_SCHEMAS, suggest_mapping, validate_mapping
from app.domains.datasets.models import (
    FILE_KINDS,
    VERSION_CANCELLED,
    VERSION_FAILED,
    VERSION_VALIDATING,
    Dataset,
    DatasetFile,
    DatasetVersion,
)
from app.domains.identity.deps import require_membership, role_checker
from app.domains.identity.models import Membership
from app.domains.jobs.models import JOB_QUEUED, VALIDATE_DATASET, Job
from app.domains.jobs.queue import enqueue

router = APIRouter(prefix="/datasets", tags=["datasets"])

_CREATE_DATA_ROLES = role_checker("owner", "admin", "analyst")


# --- schemas ---------------------------------------------------------------


def _dataset_schema(dataset: Dataset, latest: DatasetVersion | None) -> dict:
    return {
        "id": str(dataset.id),
        "name": dataset.name,
        "description": dataset.description,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "latest_version": (
            {
                "id": str(latest.id),
                "version_number": latest.version_number,
                "status": latest.status,
                "created_at": latest.created_at.isoformat() if latest.created_at else None,
            }
            if latest is not None
            else None
        ),
    }


def _file_schema(file: DatasetFile) -> dict:
    return {
        "id": str(file.id),
        "kind": file.kind,
        "original_filename": file.original_filename,
        "size_bytes": file.size_bytes,
        "sha256": file.sha256,
        "status": file.status,
        "headers": file.headers,
        "mapping": file.mapping,
        "created_at": file.created_at.isoformat() if file.created_at else None,
    }


def _version_schema(version: DatasetVersion, *, with_report: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(version.id),
        "version_number": version.version_number,
        "status": version.status,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "error_summary": version.error_summary,
    }
    if with_report:
        payload["report"] = version.report
    return payload


# --- dataset container ------------------------------------------------------


@router.post("", status_code=201)
def create_dataset(
    body: dict,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_CREATE_DATA_ROLES),
) -> dict:
    """Create a dataset container. Body: {"name": str, "description"?: str}."""
    name = (body.get("name") or "").strip()
    if not name or len(name) > 200:
        raise APIError(
            "Provide a dataset name (1-200 characters).",
            422,
            code="VALIDATION_ERROR",
            field_errors=[{"field": "name", "message": "required, 1-200 chars"}],
        )
    dataset = ds_service.create_dataset(
        db,
        org_id=membership.organization_id,
        user_id=membership.user_id,
        name=name,
        description=body.get("description"),
    )
    from app.domains.audit.service import record_audit  # SEC-06: mutations audited

    record_audit(
        db,
        org_id=membership.organization_id,
        user_id=membership.user_id,
        action="dataset.created",
        resource_type="dataset",
        resource_id=dataset.id,
        details={"name": dataset.name},
    )
    return {"dataset": _dataset_schema(dataset, None)}


@router.get("")
def list_datasets(
    db: Session = Depends(get_db),
    membership: Membership = Depends(require_membership),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    params = PageParams(page=page, page_size=page_size)
    org_id = membership.organization_id
    total = db.scalar(select(func.count()).select_from(Dataset).where(Dataset.organization_id == org_id)) or 0
    datasets = db.scalars(
        select(Dataset)
        .where(Dataset.organization_id == org_id)
        .order_by(Dataset.created_at.desc(), Dataset.id.desc())
        .offset(params.offset)
        .limit(params.page_size)
    ).all()
    latest = {d.id: ds_service.latest_version(db, d.id) for d in datasets}
    items = [_dataset_schema(d, latest.get(d.id)) for d in datasets]
    return build_page(items, params, int(total))


# --- files -------------------------------------------------------------------


@router.get("/{dataset_id}")
def get_dataset(
    dataset_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require_membership),
) -> dict:
    """Dataset detail: container, files, and version list (UI-01 groundwork)."""
    dataset = ds_service.get_scoped_dataset(db, membership.organization_id, dataset_id)
    files = db.scalars(
        select(DatasetFile).where(DatasetFile.dataset_id == dataset.id).order_by(DatasetFile.created_at)
    ).all()
    versions = db.scalars(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset.id)
        .order_by(DatasetVersion.version_number.desc())
        .limit(50)
    ).all()
    payload = _dataset_schema(dataset, versions[0] if versions else None)
    payload["files"] = [_file_schema(f) for f in files]
    payload["versions"] = [{"id": str(v.id), "version_number": v.version_number, "status": v.status} for v in versions]
    return payload


_DELETE_ROLES = role_checker("owner", "admin")


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_DELETE_ROLES),
) -> Response:
    """Delete a dataset and every derived artifact (TEST-16, PRD §11.3).

    Owner/admin only. Cascades: scores, labels, model runs (+artifacts),
    feature runs/snapshots, segment assignments/runs, versions, uploaded
    files, and jobs referencing them. Audit events are retained.
    """
    dataset = ds_service.get_scoped_dataset(db, membership.organization_id, dataset_id)
    ds_service.delete_dataset(db, dataset=dataset, user_id=membership.user_id)
    db.commit()
    return Response(status_code=204)


@router.post("/{dataset_id}/files", status_code=201)
def upload_file(
    dataset_id: uuid.UUID,
    kind: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    db: Session = Depends(get_db),
    membership: Membership = Depends(_CREATE_DATA_ROLES),
) -> dict:
    """Store a CSV file safely; returns headers and a suggested column mapping."""
    if kind not in FILE_KINDS:
        raise APIError(
            f"kind must be one of: {', '.join(FILE_KINDS)}",
            422,
            code="VALIDATION_ERROR",
            field_errors=[{"field": "kind", "message": "customers or orders"}],
        )
    dataset = ds_service.get_scoped_dataset(db, membership.organization_id, dataset_id)

    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise APIError(
            f"Upload exceeds the {settings.max_upload_mb} MB limit.",
            413,
            code="PAYLOAD_TOO_LARGE",
        )
    filename = file.filename or "upload.csv"
    relative_path, size, sha, headers = storage.save_upload(data=data, original_filename=filename)
    dataset_file = ds_service.store_file(
        db,
        dataset=dataset,
        kind=kind,
        filename=storage.sanitize_display_name(filename),
        relative_path=relative_path,
        size_bytes=size,
        sha256=sha,
        headers=headers,
        user_id=membership.user_id,
    )
    suggestion = suggest_mapping(headers, kind)
    return {"file": _file_schema(dataset_file), "suggested_mapping": suggestion}


@router.get("/{dataset_id}/files/{file_id}")
def get_file(
    dataset_id: uuid.UUID,
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require_membership),
) -> dict:
    dataset = ds_service.get_scoped_dataset(db, membership.organization_id, dataset_id)
    dataset_file = ds_service.get_scoped_file(db, membership.organization_id, dataset, file_id)
    return {
        "file": _file_schema(dataset_file),
        "suggested_mapping": suggest_mapping(dataset_file.headers, dataset_file.kind),
    }


@router.patch("/{dataset_id}/files/{file_id}")
def set_mapping(
    dataset_id: uuid.UUID,
    file_id: uuid.UUID,
    body: dict,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_CREATE_DATA_ROLES),
) -> dict:
    """Set the canonical column mapping on a file (DATA-08)."""
    mapping = body.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise APIError(
            "mapping must be a non-empty object.",
            422,
            code="VALIDATION_ERROR",
            field_errors=[{"field": "mapping", "message": "object {canonical: source}"}],
        )
    dataset = ds_service.get_scoped_dataset(db, membership.organization_id, dataset_id)
    dataset_file = ds_service.get_scoped_file(db, membership.organization_id, dataset, file_id)
    if not dataset_file.headers:
        raise APIError("Source file has no headers; re-upload the file.", 409, code="CONFLICT")

    normalized_headers = {h.strip().lower() for h in dataset_file.headers}
    clean = {k: (v or "").strip() for k, v in mapping.items()}
    missing = [src for src in clean.values() if src and src.lower() not in normalized_headers]
    if missing:
        raise APIError(
            "Mapped source column(s) not present in the file: {}".format(", ".join(missing)),
            422,
            code="VALIDATION_ERROR",
            field_errors=[{"field": "mapping", "message": f"missing column(s): {', '.join(missing)}"}],
        )
    try:
        validate_mapping(dataset_file.kind, clean)
    except ValueError as exc:
        raise APIError(
            str(exc),
            422,
            code="VALIDATION_ERROR",
            field_errors=[{"field": "mapping", "message": str(exc)}],
        ) from None

    ds_service.attach_mapping(dataset_file, clean)
    required = CANONICAL_SCHEMAS[dataset_file.kind]["required"]
    ready = all(clean.get(c) for c in required)
    return {"file": _file_schema(dataset_file), "ready_for_version": ready}


# --- versions (immutable dataset versions, DATA-11/12) ------------------------


@router.post("/{dataset_id}/versions", status_code=202)
def create_version(
    dataset_id: uuid.UUID,
    body: dict,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_CREATE_DATA_ROLES),
) -> dict:
    """Bind one customers + one orders file into a new version and start validation."""
    dataset = ds_service.get_scoped_dataset(db, membership.organization_id, dataset_id)
    try:
        file_ids = {
            "customers": uuid.UUID(str(body["customers_file_id"])),
            "orders": uuid.UUID(str(body["orders_file_id"])),
        }
    except (KeyError, ValueError):
        raise APIError(
            "customers_file_id and orders_file_id are required.",
            422,
            code="VALIDATION_ERROR",
        ) from None
    version = ds_service.create_version(db, dataset=dataset, user_id=membership.user_id, file_ids=file_ids)
    job = enqueue(
        db,
        job_type=VALIDATE_DATASET,
        organization_id=membership.organization_id,
        created_by_user_id=membership.user_id,
        payload={"version_id": str(version.id)},
        correlation_id=request_id_var.get(),
    )
    from app.domains.audit.service import record_audit  # SEC-06: mutations audited

    record_audit(
        db,
        org_id=membership.organization_id,
        user_id=membership.user_id,
        action="dataset.version_created",
        resource_type="dataset_version",
        resource_id=version.id,
        details={"dataset_id": str(dataset.id)},
    )
    db.commit()
    version = ds_service.get_scoped_version(db, membership.organization_id, dataset, version.id)
    return {"version": _version_schema(version), "job_id": str(job.id)}


@router.get("/{dataset_id}/versions")
def list_versions(
    dataset_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require_membership),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    dataset = ds_service.get_scoped_dataset(db, membership.organization_id, dataset_id)
    params = PageParams(page=page, page_size=page_size)
    base = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)
    total = (
        db.scalar(select(func.count()).select_from(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)) or 0
    )
    versions = db.scalars(
        base.order_by(DatasetVersion.version_number.desc()).offset(params.offset).limit(params.page_size)
    ).all()
    return build_page([_version_schema(v) for v in versions], params, int(total))


@router.get("/{dataset_id}/versions/{version_id}")
def get_version(
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require_membership),
) -> dict:
    dataset = ds_service.get_scoped_dataset(db, membership.organization_id, dataset_id)
    version = ds_service.get_scoped_version(db, membership.organization_id, dataset, version_id)
    job = _latest_job_for(db, membership.organization_id, str(version.id))
    payload = _version_schema(version, with_report=True)
    payload["job_id"] = str(job.id) if job is not None else None
    if job is not None and version.status == VERSION_VALIDATING:
        payload["job_status"] = job.status
        payload["progress"] = job.progress
    return payload


@router.get("/{dataset_id}/versions/{version_id}/eda")
def get_version_eda(
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require_membership),
) -> dict:
    """Reproducible EDA report for a dataset version (FE-01)."""
    dataset = ds_service.get_scoped_dataset(db, membership.organization_id, dataset_id)
    version = ds_service.get_scoped_version(db, membership.organization_id, dataset, version_id)

    customers_df, orders_df = ds_service.load_version_frames(db, version)
    report = compute_eda(customers_df, orders_df)
    report["dataset_version_id"] = str(version.id)
    report["version_number"] = version.version_number
    return {"eda": report}


def _latest_job_for(db: Session, org_id: uuid.UUID, version_id: str) -> Job | None:
    """Newest validate_dataset job for a version (payload filtered in Python,
    staying portable across SQLite/Postgres JSON handling)."""
    jobs = db.scalars(
        select(Job)
        .where(Job.job_type == VALIDATE_DATASET, Job.organization_id == org_id)
        .order_by(Job.created_at.desc())
        .limit(50)
    ).all()
    return next((j for j in jobs if str(j.payload.get("version_id")) == version_id), None)


@router.post("/{dataset_id}/versions/{version_id}/cancel")
def cancel_version(
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_CREATE_DATA_ROLES),
) -> dict:
    """Cancel a still-queued validation (DATA-13; running jobs finish their unit)."""
    dataset = ds_service.get_scoped_dataset(db, membership.organization_id, dataset_id)
    version = ds_service.get_scoped_version(db, membership.organization_id, dataset, version_id)
    if version.status != VERSION_VALIDATING:
        raise APIError(
            f"Version is already '{version.status}'; nothing to cancel.",
            409,
            code="CONFLICT",
        )
    job = _latest_job_for(db, membership.organization_id, str(version.id))
    if job is not None and job.status == JOB_QUEUED:
        job.status = "cancelled"
        version.status = VERSION_CANCELLED
        db.commit()
        return {"version": _version_schema(version)}
    raise APIError(
        "The job already started and cannot be cancelled.",
        409,
        code="CONFLICT",
    )


@router.post("/{dataset_id}/versions/{version_id}/retry")
def retry_version(
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(_CREATE_DATA_ROLES),
) -> dict:
    """Safe retry of a failed version (DATA-13): re-validates the same files."""
    dataset = ds_service.get_scoped_dataset(db, membership.organization_id, dataset_id)
    version = ds_service.get_scoped_version(db, membership.organization_id, dataset, version_id)
    if version.status != VERSION_FAILED:
        raise APIError(
            "Only failed versions can be retried.",
            409,
            code="CONFLICT",
        )
    version.status = VERSION_VALIDATING
    version.error_summary = None
    job = enqueue(
        db,
        job_type=VALIDATE_DATASET,
        organization_id=membership.organization_id,
        created_by_user_id=membership.user_id,
        payload={"version_id": str(version.id)},
        correlation_id=request_id_var.get(),
    )
    db.commit()
    return {"version": _version_schema(version), "job_id": str(job.id)}


# --- templates (DATA-05) ------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"


@router.get("/templates/{kind}")
def download_template(kind: str, response: Response) -> Response:
    """Download a canonical CSV template that passes the validator (DATA-05)."""
    if kind not in FILE_KINDS:
        raise APIError("kind must be 'customers' or 'orders'.", 404, code="NOT_FOUND")
    path = _TEMPLATES_DIR / f"{kind}_template.csv"
    if not path.exists():
        rows = CANONICAL_SCHEMAS[kind]
        headers = ",".join(rows["required"] + rows["optional"])
        content = headers + "\n"
    else:
        content = path.read_text(encoding="utf-8")
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{kind}_template.csv"'},
    )
