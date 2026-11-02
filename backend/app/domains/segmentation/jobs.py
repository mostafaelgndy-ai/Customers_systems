"""Segmentation job handler (JOB-05 groundwork; SEG-09).

`run_segmentation` executes a queued segmentation run: fits preprocessing
and K-Means, evaluates the DBSCAN alternative, persists assignments and
profiles, and updates the run row.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.domains.jobs.queue import register_handler
from app.domains.segmentation import service as segmentation_service

RUN_SEGMENTATION = "run_segmentation"


def handle_run_segmentation(db: Session, job: Any) -> dict[str, Any]:
    run_id = uuid.UUID(str(job.payload["run_id"]))
    return segmentation_service.execute_segmentation_run(db, run_id=run_id)


def register() -> None:
    register_handler(RUN_SEGMENTATION, handle_run_segmentation)


register()
