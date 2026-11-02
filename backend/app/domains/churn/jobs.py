"""Churn job handlers.

- `train_churn_model`: trains the LR baseline + RF benchmark for one
  churn model run (CHURN-05..12): derives the time-aware split,
  evaluates, saves artifacts and metadata, and updates the run row.
- `score_churn_model`: batches predictions for all eligible customers
  from a succeeded model run (CHURN-13, CHURN-14), stores scores with
  explanations (CHURN-16) referencing the immutable model run (CHURN-17).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.domains.churn import service as churn_service
from app.domains.jobs.queue import register_handler

TRAIN_CHURN_MODEL = "train_churn_model"
SCORE_CHURN_MODEL = "score_churn_model"


def handle_train_churn_model(db: Session, job: Any) -> dict[str, Any]:
    run_id = uuid.UUID(str(job.payload["run_id"]))
    return churn_service.execute_model_run(db, run_id=run_id)


def handle_score_churn_model(db: Session, job: Any) -> dict[str, Any]:
    return churn_service.execute_score_run(
        db,
        model_run_id=uuid.UUID(str(job.payload["model_run_id"])),
        low_band_max=float(job.payload.get("low_band_max", 0.30)),
        high_band_min=float(job.payload.get("high_band_min", 0.70)),
        scoring_run_id=str(job.id),
    )


def register() -> None:
    register_handler(TRAIN_CHURN_MODEL, handle_train_churn_model)
    register_handler(SCORE_CHURN_MODEL, handle_score_churn_model)


register()
