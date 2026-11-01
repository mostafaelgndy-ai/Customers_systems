"""Dataset service layer: datasets, files, versions, and state rules.

Enforces tenant scoping and version immutability rules (DATA-06..12).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domains.datasets.models import (
    FILE_KIND_CUSTOMERS,
    FILE_KIND_ORDERS,
    FILE_KINDS,
    VERSION_VALIDATING,
    Dataset,
    DatasetFile,
    DatasetVersion,
    DatasetVersionFile,
)


def get_scoped_dataset(db: Session, org_id: uuid.UUID, dataset_id: uuid.UUID) -> Dataset:
    dataset = db.scalar(select(Dataset).where(Dataset.id == dataset_id, Dataset.organization_id == org_id))
    if dataset is None:
        # 404 by design: another organization's dataset is indistinguishable
        # from a missing one (docs/api-conventions.md §3).
        raise APIError("Dataset not found.", 404, code="NOT_FOUND")
    return dataset


def get_scoped_file(db: Session, org_id: uuid.UUID, dataset: Dataset, file_id: uuid.UUID) -> DatasetFile:
    file = db.scalar(
        select(DatasetFile).where(
            DatasetFile.id == file_id,
            DatasetFile.dataset_id == dataset.id,
            DatasetFile.organization_id == org_id,
        )
    )
    if file is None:
        raise APIError("File not found.", 404, code="NOT_FOUND")
    return file


def get_scoped_version(db: Session, org_id: uuid.UUID, dataset: Dataset, version_id: uuid.UUID) -> DatasetVersion:
    version = db.scalar(
        select(DatasetVersion).where(
            DatasetVersion.id == version_id,
            DatasetVersion.dataset_id == dataset.id,
            DatasetVersion.organization_id == org_id,
        )
    )
    if version is None:
        raise APIError("Version not found.", 404, code="NOT_FOUND")
    return version


def next_version_number(db: Session, dataset_id: uuid.UUID) -> int:
    current = db.scalar(
        select(DatasetVersion.version_number)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_number.desc())
        .limit(1)
    )
    return (current or 0) + 1


def create_version(
    db: Session,
    *,
    dataset: Dataset,
    user_id: uuid.UUID,
    file_ids: dict[str, uuid.UUID],
) -> DatasetVersion:
    """Create an immutable version binding one customers + one orders file.

    Re-uploading and re-validating creates a NEW version; previous
    versions and their reports remain untouched (DATA-11). A failed
    validation can never overwrite a previous successful version (PRD §8.2).
    """
    files: dict[str, DatasetFile] = {}
    for kind, file_id in file_ids.items():
        if kind not in FILE_KINDS:
            raise APIError(f"Unknown file kind '{kind}'.", 400, code="BAD_REQUEST")
        file = get_scoped_file(db, dataset.organization_id, dataset, file_id)
        if file.kind != kind:
            raise APIError(
                f"File {file_id} is a '{file.kind}' file, not '{kind}'.",
                409,
                code="CONFLICT",
            )
        if file.status != "uploaded" and file.status != "mapped":
            raise APIError("File cannot be attached to a new version.", 409, code="CONFLICT")
        if file.kind in files:
            raise APIError(
                f"More than one '{file.kind}' file was selected for this version.",
                409,
                code="CONFLICT",
            )
        files[file.kind] = file

    missing = [k for k in (FILE_KIND_CUSTOMERS, FILE_KIND_ORDERS) if k not in files]
    if missing:
        kinds = " and ".join(f"one {k}" for k in (FILE_KIND_CUSTOMERS, FILE_KIND_ORDERS))
        raise APIError(
            f"A dataset version requires exactly {kinds}.",
            422,
            code="VALIDATION_ERROR",
        )
    for file in files.values():
        if not file.mapping:
            raise APIError(
                f"The {file.kind} file has no column mapping yet; set its mapping first so it can be canonicalized.",
                422,
                code="VALIDATION_ERROR",
                field_errors=[{"field": f"{file.kind}_file_id", "message": "column mapping missing"}],
            )

    version = DatasetVersion(
        dataset_id=dataset.id,
        organization_id=dataset.organization_id,
        version_number=next_version_number(db, dataset.id),
        status=VERSION_VALIDATING,
        created_by_user_id=user_id,
    )
    db.add(version)
    db.flush()

    db.add_all(
        DatasetVersionFile(
            dataset_version_id=version.id,
            dataset_file_id=file.id,
            file_kind=kind,
        )
        for kind, file in files.items()
    )
    return version


def create_dataset(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    description: str | None = None,
) -> Dataset:
    dataset = Dataset(
        organization_id=org_id,
        name=name,
        description=description,
        created_by_user_id=user_id,
    )
    db.add(dataset)
    db.flush()
    return dataset


def store_file(
    db: Session,
    *,
    dataset: Dataset,
    kind: str,
    filename: str,
    relative_path: str,
    size_bytes: int,
    sha256: str,
    headers: list,
    user_id: uuid.UUID,
) -> DatasetFile:
    dataset_file = DatasetFile(
        dataset_id=dataset.id,
        organization_id=dataset.organization_id,
        kind=kind,
        original_filename=filename,
        storage_path=relative_path,
        size_bytes=size_bytes,
        sha256=sha256,
        headers=headers,
        status="uploaded",
        created_by_user_id=user_id,
    )
    db.add(dataset_file)
    db.flush()
    return dataset_file


def latest_version(db: Session, dataset_id: uuid.UUID) -> DatasetVersion | None:
    return db.scalar(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_number.desc())
        .limit(1)
    )


def attach_mapping(file: DatasetFile, mapping: dict[str, str]) -> None:
    """Store a validated canonical mapping on a file (mutates only pre-version files)."""
    file.mapping = mapping
    file.status = "mapped"


def can_cancel(version: DatasetVersion) -> bool:
    return version.status == VERSION_VALIDATING
