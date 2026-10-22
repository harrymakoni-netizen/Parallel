"""Celery tasks for report generation."""

from __future__ import annotations

from parallel_core.jobs import TASK_REPORT, celery_app

from .generate import generate_report


@celery_app.task(name=TASK_REPORT, acks_late=True)
def generate(report_id: str) -> str:
    return generate_report(report_id)
