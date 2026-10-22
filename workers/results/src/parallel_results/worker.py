"""Celery entry point: celery -A parallel_results.worker worker -Q results -c 2"""

from parallel_core.jobs import celery_app as app

from . import tasks  # noqa: F401  (registers tasks)

__all__ = ["app"]
