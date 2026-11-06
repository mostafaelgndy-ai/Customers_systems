"""Dataset service layer: datasets, files, versions, and state rules.

Enforces tenant scoping and version immutability rules (DATA-06..12).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

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


def get_scoped_version_by_id(db: Session, org_id: uuid.UUID, version_id: uuid.UUID) -> DatasetVersion:
    """Load a dataset version by id, scoped to the organization only."""
    version = db.scalar(
        select(DatasetVersion).where(
            DatasetVersion.id == version_id,
            DatasetVersion.organization_id == org_id,
        )
    )
    if version is None:
        raise APIError("Dataset version not found.", 404, code="NOT_FOUND")
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


def load_version_frames(db: Session, version: DatasetVersion) -> tuple:
    """Canonicalize a version's customers and orders files into DataFrames.

    Raises CONFLICT when either file is missing from the version.
    """
    import pandas as pd

    from app.domains.datasets.storage import read_file_bytes
    from app.domains.datasets.validation import canonicalize

    frames: dict[str, pd.DataFrame] = {}
    for link in version.file_links:
        file = db.scalar(select(DatasetFile).where(DatasetFile.id == link.dataset_file_id))
        if file is None:
            continue
        frames[link.file_kind] = canonicalize(read_file_bytes(file.storage_path), link.file_kind, file.mapping or {})
    missing = [kind for kind in ("customers", "orders") if kind not in frames]
    unexpected = [kind for kind in frames if kind not in ("customers", "orders")]
    if missing or unexpected:
        raise APIError(
            "Dataset version requires exactly one customers and one orders file.",
            409,
            code="CONFLICT",
        )
    return frames["customers"], frames["orders"]


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


def delete_dataset(db: Session, *, dataset: Dataset, user_id: uuid.UUID) -> dict[str, int]:
    """Delete a dataset and every artifact derived from its versions (TEST-16).

    Cascade (children first): scores -> labels -> model runs (incl. on-disk
    artifacts) -> segment assignments -> segmentation runs -> snapshots ->
    feature runs -> version files -> versions -> uploaded files (incl. on-disk
    bytes). Job rows whose payload/result reference the dataset are removed
    too, and export CSV files produced from deleted runs are unlinked.
    Audit events are intentionally retained (security log, org-scoped).
    """
    from app.domains.audit.service import record_audit
    from app.domains.churn.models import ChurnLabel, ChurnModelRun, ChurnScore
    from app.domains.features.models import FeatureRun, FeatureSnapshot
    from app.domains.jobs.models import JOB_SUCCEEDED, Job
    from app.domains.segmentation.models import SegmentAssignment, SegmentationRun

    org_id = dataset.organization_id
    versions = list(db.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)))
    version_ids = [v.id for v in versions]

    counts: dict[str, int] = {}
    # every derived artifact id: job payloads (exports, scoring, …) may carry
    # any of these instead of the dataset id
    derived_ids: set[str] = {str(v) for v in version_ids} | {str(dataset.id)}
    seg_artifacts: Sequence[str | None] = []

    # --- churn ------------------------------------------------------------
    run_ids = list(
        db.scalars(
            select(ChurnModelRun.id).where(
                ChurnModelRun.dataset_version_id.in_(version_ids),
                ChurnModelRun.organization_id == org_id,
            )
        )
    )
    if run_ids:
        counts["churn_scores"] = (
            db.query(ChurnScore)
            .where(ChurnScore.model_run_id.in_(run_ids), ChurnScore.organization_id == org_id)
            .delete(synchronize_session=False)
        )
        derived_ids.update(str(r) for r in run_ids)
        # artifacts on disk referenced by the deleted runs
        artifact_paths = db.scalars(select(ChurnModelRun.artifact_path).where(ChurnModelRun.id.in_(run_ids))).all()
        counts["artifacts_removed"] = _unlink_files(artifact_paths)
        counts["churn_model_runs"] = (
            db.query(ChurnModelRun).where(ChurnModelRun.id.in_(run_ids)).delete(synchronize_session=False)
        )

    if version_ids:
        counts["churn_labels"] = (
            db.query(ChurnLabel)
            .where(
                ChurnLabel.dataset_version_id.in_(version_ids),
                ChurnLabel.organization_id == org_id,
            )
            .delete(synchronize_session=False)
        )

        # --- segmentation -------------------------------------------------
        seg_run_ids = list(
            db.scalars(
                select(SegmentationRun.id).where(
                    SegmentationRun.dataset_version_id.in_(version_ids),
                    SegmentationRun.organization_id == org_id,
                )
            )
        )
        seg_artifacts = []
        if seg_run_ids:
            derived_ids.update(str(r) for r in seg_run_ids)
            counts["segment_assignments"] = (
                db.query(SegmentAssignment)
                .where(
                    SegmentAssignment.segmentation_run_id.in_(seg_run_ids),
                    SegmentAssignment.organization_id == org_id,
                )
                .delete(synchronize_session=False)
            )
            seg_artifacts = db.scalars(
                select(SegmentationRun.artifact_path).where(SegmentationRun.id.in_(seg_run_ids))
            ).all()
            counts["segmentation_runs"] = (
                db.query(SegmentationRun).where(SegmentationRun.id.in_(seg_run_ids)).delete(synchronize_session=False)
            )

        # --- features -------------------------------------------------------
        feature_run_ids = list(
            db.scalars(
                select(FeatureRun.id).where(
                    FeatureRun.dataset_version_id.in_(version_ids),
                    FeatureRun.organization_id == org_id,
                )
            )
        )
        if feature_run_ids:
            derived_ids.update(str(r) for r in feature_run_ids)
            counts["feature_snapshots"] = (
                db.query(FeatureSnapshot)
                .where(
                    FeatureSnapshot.feature_run_id.in_(feature_run_ids),
                    FeatureSnapshot.organization_id == org_id,
                )
                .delete(synchronize_session=False)
            )
            counts["feature_runs"] = (
                db.query(FeatureRun).where(FeatureRun.id.in_(feature_run_ids)).delete(synchronize_session=False)
            )

        counts["version_files"] = (
            db.query(DatasetVersionFile)
            .where(DatasetVersionFile.dataset_version_id.in_(version_ids))
            .delete(synchronize_session=False)
        )
        counts["versions"] = (
            db.query(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id).delete(synchronize_session=False)
        )

    # --- uploaded bytes -----------------------------------------------------
    upload_paths = db.scalars(select(DatasetFile.storage_path).where(DatasetFile.dataset_id == dataset.id)).all()
    for relative in upload_paths:
        from contextlib import suppress

        from app.domains.datasets.storage import delete_file

        # path no longer resolvable is fine; the DB row is the source of truth
        with suppress(APIError):
            delete_file(relative)
    counts["uploaded_files"] = (
        db.query(DatasetFile).where(DatasetFile.dataset_id == dataset.id).delete(synchronize_session=False)
    )
    counts["artifacts_removed"] = counts.get("artifacts_removed", 0) + _unlink_files(seg_artifacts)

    # --- jobs referencing the dataset or any derived run --------------------
    job_rows = list(db.scalars(select(Job).where(Job.organization_id == org_id)).all())
    job_files: list[str] = []
    jobs_removed = 0
    for job in job_rows:
        blob = str(job.payload) + str(job.result)
        if any(token in blob for token in derived_ids):
            if job.job_type == "export_csv" and job.status == JOB_SUCCEEDED:
                export_path = (job.result or {}).get("file_path", "")
                if export_path:
                    job_files.append(export_path)
            db.delete(job)
            jobs_removed += 1
    counts["jobs"] = jobs_removed
    counts["artifacts_removed"] = counts.get("artifacts_removed", 0) + _unlink_files(job_files)

    db.delete(dataset)
    record_audit(
        db,
        org_id=org_id,
        user_id=user_id,
        action="dataset.deleted",
        resource_type="dataset",
        resource_id=dataset.id,
        details={"counts": counts},
    )
    return counts


def _unlink_files(paths: Sequence[str | None]) -> int:
    """Safely remove on-disk artifacts (model/export files); returns count."""
    from pathlib import Path

    from app.core.config import settings

    uploads_root = Path(settings.upload_dir).resolve()
    removed = 0
    for raw in paths or []:
        if not raw:
            continue
        candidate = Path(raw)
        # Defense in depth: uploaded CSVs are handled via delete_file(); only
        # artifact files outside the uploads tree are unlinked here.
        if candidate.is_file() and uploads_root not in candidate.resolve().parents:
            try:
                candidate.unlink()
                removed += 1
            except OSError:
                pass
    return removed
