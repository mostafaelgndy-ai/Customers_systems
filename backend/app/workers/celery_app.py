"""Celery application (JOB-01). Queen only; tasks live in app.workers.tasks."""

from __future__ import annotations

from celery import Celery

from app.core.config import settings

app = Celery(
    "customeriq",
    broker=settings.redis_url,
    backend="db+" + settings.database_url,
    include=["app.workers.tasks"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Network hiccups must not stop workers (PRD §8.2 reliability):
    broker_connection_retry_on_startup=True,
    worker_max_tasks_per_child=50,
)
