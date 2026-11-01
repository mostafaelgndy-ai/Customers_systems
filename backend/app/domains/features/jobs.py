"""The `compute_features` job handler (JOB-03, JOB-07..09).

Computes customer-level behavioral features (RFM, tenure, AOV, rolling
windows) from a validated dataset version. Idempotent and tenant-isolated.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.datasets.models import DatasetFile, DatasetVersionFile
from app.domains.datasets.storage import read_file_bytes
from app.domains.datasets.validation import canonicalize
from app.domains.features.models import FeatureRun, FeatureSnapshot
from app.domains.features.pipeline import FeatureConfig, compute_features
from app.domains.jobs.queue import register_handler

COMPUTE_FEATURES = "compute_features"


def handle_compute_features(db: Session, job) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Compute features for a dataset version and persist the snapshot.

    Idempotent (JOB-08): if the run already left `queued`/`failed`, the job
    re-executes but overwrites the same run's snapshot — never creates
    duplicates (FE-08).
    """
    run_id = uuid.UUID(str(job.payload["run_id"]))
    run = db.get(FeatureRun, run_id)
    if run is None:
        raise ValueError(f"Feature run {run_id} not found")

    if run.status not in ("queued", "failed"):
        return {"skipped": True, "reason": f"run already {run.status}"}

    run.status = "running"
    db.commit()

    try:
        version_id = run.dataset_version_id

        file_links = db.scalars(
            select(DatasetVersionFile).where(DatasetVersionFile.dataset_version_id == version_id)
        ).all()

        customers_bytes = b""
        orders_bytes = b""
        customers_mapping = {}
        orders_mapping = {}

        for link in file_links:
            file = db.get(DatasetFile, link.dataset_file_id)
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

        customers_df = canonicalize(customers_bytes, "customers", customers_mapping)
        orders_df = canonicalize(orders_bytes, "orders", orders_mapping)

        config = FeatureConfig(
            cutoff_date=run.cutoff_date,
            observation_days=run.observation_days,
        )
        features = compute_features(customers_df, orders_df, config)

        # Remove any existing snapshot for this run (idempotent, FE-08)
        existing = db.scalars(select(FeatureSnapshot).where(FeatureSnapshot.feature_run_id == run_id)).all()
        for snap in existing:
            db.delete(snap)

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

        run.status = "succeeded"
        run.row_count = len(features)
        db.commit()

        return {
            "run_id": str(run.id),
            "status": run.status,
            "row_count": run.row_count,
            "cutoff_date": run.cutoff_date,
            "feature_version": run.feature_version,
        }

    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)[:500]
        db.commit()
        raise


def register() -> None:
    register_handler(COMPUTE_FEATURES, handle_compute_features)


register()
