"""Celery tasks for capture processing and reconstruction."""

from __future__ import annotations

from parallel_core.jobs import TASK_RECONSTRUCT, celery_app

from .runner import execute_job


@celery_app.task(name=TASK_RECONSTRUCT, acks_late=True)
def reconstruct(job_id: str) -> str:
    return execute_job(job_id)
