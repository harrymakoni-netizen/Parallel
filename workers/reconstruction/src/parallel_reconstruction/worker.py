"""Celery entry point: celery -A parallel_reconstruction.worker worker -Q reconstruction,gpu -c 1"""

from parallel_core.jobs import celery_app as app

from . import tasks  # noqa: F401

__all__ = ["app"]
