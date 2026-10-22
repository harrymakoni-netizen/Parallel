"""Celery application shared by the API (task dispatch) and workers (task execution).

Queues:
  cpu            - managed CPU engine runs, validation, normalisation
  gpu            - GPU-dependent work (reconstruction training, Isaac Sim)
  reconstruction - capture processing stages
  results        - report generation
  maintenance    - recovery sweeps (beat schedule)
The API sends tasks by name, so it never imports worker code.
"""

from __future__ import annotations

from celery import Celery

from .settings import get_settings

TASK_EXECUTE_RUN = "parallel.simulation.execute_run"
TASK_RECONSTRUCT = "parallel.reconstruction.execute_job"
TASK_REPORT = "parallel.results.generate_report"
TASK_RECOVERY = "parallel.simulation.recovery_sweep"
TASK_OPTIMISATION = "parallel.simulation.run_optimisation"
TASK_IMPORT_RESULT = "parallel.simulation.import_external_result"

GPU_ENGINES = {"isaacsim"}


def make_celery() -> Celery:
    s = get_settings()
    app = Celery("parallel", broker=s.redis_url, backend=s.redis_url)
    app.conf.update(
        task_acks_late=True,  # a task is re-delivered if its worker dies before acknowledging
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_always_eager=s.task_always_eager,
        task_eager_propagates=True,
        task_default_queue="cpu",
        task_routes={
            TASK_RECONSTRUCT: {"queue": "reconstruction"},
            TASK_REPORT: {"queue": "results"},
            TASK_RECOVERY: {"queue": "maintenance"},
        },
        beat_schedule={"recovery-sweep": {"task": TASK_RECOVERY, "schedule": 60.0}},
        broker_connection_retry_on_startup=True,
        result_expires=24 * 3600,
    )
    return app


celery_app = make_celery()


def queue_for_engine(engine_id: str) -> str:
    return "gpu" if engine_id in GPU_ENGINES else "cpu"
