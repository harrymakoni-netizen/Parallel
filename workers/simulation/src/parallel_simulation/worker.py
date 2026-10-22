"""Celery entry point for the simulation worker."""

from parallel_core.jobs import celery_app as app

from . import tasks  # noqa: F401  (registers tasks)

__all__ = ["app"]
