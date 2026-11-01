"""Feature service layer (FE-01, FE-08, FE-10).

Coordinates feature computation, run lifecycle, and snapshot persistence.
All runs are organization-scoped and traceable to a dataset version.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domains.datasets.models import VERSION_VALID
from app.domains.datasets.service import get_scoped_version
from app.domains.datasets.storage import read_file_bytes
from app.domains.features.models import (
    FEATURE_RUN_FAILED,
    FEATURE_RUN_QUEUED,
    FEATURE_RUN_SUCCEEDED,
    FeatureRun,
    FeatureSnapshot,
)
from app.domains.features.pipeline import FeatureConfig, compute_features, feature_schema
from app.domains.identity.deps import role_checker
from app.domains.jobs.queue import enqueue

_FEATURE_RUN_ROLES = role_checker("owner", "admin", "analyst")


def get_scoped_run(db: Session, org_id: uuid.UUID, dataset_version_id: uuid.UUID, run_id: uuid.UUID) -> FeatureRun:
    run = db.scalar(
        select(FeatureRun).where(
            FeatureRun.id == run_id,
            FeatureRun.organization_id == org_id,
            FeatureRun.dataset_version_id == dataset_version_id,
        )
    )
    if run is None:
        raise APIError("Feature run not found.", 404, code="NOT_FOUND")
    return run


def create_feature_run(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    cutoff_date: str,
    observation_days: int = 180,
) -> FeatureRun:
    """Create a new feature computation run (FE-08, FE-10).

    Validates that the dataset version is valid before queueing the run.
    """
    version = get_scoped_version(db, org_id, None, dataset_version_id)  # type: ignore[arg-type]
    if version.status != VERSION_VALID:
        raise APIError(
            f"Dataset version is '{version.status}'; must be 'valid' to compute features.",
            409,
            code="CONFLICT",
        )

    # Validate cutoff date is parseable
    try:
        datetime.fromisoformat(cutoff_date)
    except ValueError as exc:
        raise APIError("Invalid cutoff_date; use ISO format (YYYY-MM-DD).", 400, code="BAD_REQUEST") from exc

    run = FeatureRun(
        organization_id=org_id,
        dataset_version_id=dataset_version_id,
        cutoff_date=cutoff_date,
        observation_days=observation_days,
        feature_version="1.0.0",
        status=FEATURE_RUN_QUEUED,
        created_by_user_id=user_id,
    )
    db.add(run)
    db.flush()

    # Enqueue background job for async computation
    enqueue(
        db,
        job_type="compute_features",
        organization_id=org_id,
        created_by_user_id=user_id,
        payload={
            "run_id": str(run.id),
            "dataset_version_id": str(dataset_version_id),
            "cutoff_date": cutoff_date,
            "observation_days": observation_days,
        },
    )
    db.commit()
    return run


def execute_feature_run(
    db: Session,
    *,
    run_id: uuid.UUID,
) -> dict[str, Any]:
    """Execute a feature run synchronously (used by background worker).

    Loads the dataset version files, computes features, and persists
    the snapshot. Idempotent: re-running overwrites the same run's
    snapshot but never creates duplicates (FE-08).
    """
    run = db.scalar(select(FeatureRun).where(FeatureRun.id == run_id))
    if run is None:
        raise APIError("Feature run not found.", 404, code="NOT_FOUND")

    run.status = "running"
    db.commit()

    try:
        # Load the dataset version files
        version = get_scoped_version(db, run.organization_id, None, run.dataset_version_id)  # type: ignore[arg-type]

        # Read customer and order file bytes
        file_links = version.file_links
        customers_bytes = b""
        orders_bytes = b""
        customers_mapping = {}
        orders_mapping = {}

        from app.domains.datasets.models import DatasetFile

        for link in file_links:
            file = db.scalar(select(DatasetFile).where(DatasetFile.id == link.dataset_file_id))
            if file is None:
                continue
            raw_bytes = read_file_bytes(file.storage_path)
            if link.file_kind == "customers":
                customers_bytes = raw_bytes
                customers_mapping = file.mapping or {}
            elif link.file_kind == "orders":
                orders_bytes = raw_bytes
                orders_mapping = file.mapping or {}

        if not customers_bytes or not orders_bytes:
            raise ValueError("Dataset version missing customer or order file data")

        # Canonicalize using the validation engine
        from app.domains.datasets.validation import canonicalize

        customers_df = canonicalize(customers_bytes, "customers", customers_mapping)
        orders_df = canonicalize(orders_bytes, "orders", orders_mapping)

        # Compute features
        config = FeatureConfig(
            cutoff_date=run.cutoff_date,
            observation_days=run.observation_days,
        )
        features = compute_features(customers_df, orders_df, config)

        # Persist snapshot
        snapshot = FeatureSnapshot(
            feature_run_id=run.id,
            organization_id=run.organization_id,
            dataset_version_id=run.dataset_version_id,
            cutoff_date=run.cutoff_date,
            feature_version=run.feature_version,
            features=features,
            created_by_user_id=run.created_by_user_id,
        )
        db.add(snapshot)

        run.status = FEATURE_RUN_SUCCEEDED
        run.row_count = len(features)
        db.commit()

        return {
            "run_id": str(run.id),
            "status": run.status,
            "row_count": run.row_count,
            "cutoff_date": run.cutoff_date,
            "observation_days": run.observation_days,
            "feature_version": run.feature_version,
            "features": features,
        }

    except Exception as exc:  # noqa: BLE001 - capture all for run failure
        run.status = FEATURE_RUN_FAILED
        run.error = str(exc)
        db.commit()
        raise


def list_runs(
    db: Session,
    org_id: uuid.UUID,
    dataset_version_id: uuid.UUID | None = None,
) -> list[FeatureRun]:
    """List feature runs for an organization, optionally filtered by version."""
    query = select(FeatureRun).where(FeatureRun.organization_id == org_id)
    if dataset_version_id is not None:
        query = query.where(FeatureRun.dataset_version_id == dataset_version_id)
    query = query.order_by(FeatureRun.created_at.desc())
    return list(db.scalars(query).all())


def get_schema() -> dict[str, dict[str, str]]:
    """Return the canonical feature schema (FE-07)."""
    return feature_schema()
