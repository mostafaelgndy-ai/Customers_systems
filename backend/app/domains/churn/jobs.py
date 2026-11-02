"""The `train_churn_model` job handler.

Trains the LR baseline + RF benchmark for one churn model run
(CHURN-05..12): derives the time-aware split, evaluates, saves artifacts
and metadata, and updates the run row.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.domains.churn import service as churn_service
from app.domains.jobs.queue import register_handler

TRAIN_CHURN_MODEL = "train_churn_model"


def handle_train_churn_model(db: Session, job: Any) -> dict[str, Any]:
    run_id = uuid.UUID(str(job.payload["run_id"]))
    return churn_service.execute_model_run(db, run_id=run_id)


def register() -> None:
    register_handler(TRAIN_CHURN_MODEL, handle_train_churn_model)


register()
