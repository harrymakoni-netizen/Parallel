"""Celery tasks for engine execution, external-result import and recovery."""

from __future__ import annotations

from celery.signals import worker_ready, worker_shutdown

from parallel_core import engine_status, runs
from parallel_core.jobs import (
    TASK_ENGINE_STATUS,
    TASK_EXECUTE_RUN,
    TASK_IMPORT_RESULT,
    TASK_OPTIMISATION,
    TASK_RECOVERY,
    celery_app,
)


@celery_app.task(name=TASK_EXECUTE_RUN, acks_late=True)
def execute_run(run_id: str) -> str:
    return runs.execute_run(run_id)


@celery_app.task(name=TASK_IMPORT_RESULT, acks_late=True)
def import_external_result(
    run_id: str,
    package_key: str,
    user_id: str,
    external_installation: str,
    supplied_provenance: str,
    options: dict,
) -> str:
    return runs.import_external(
        run_id,
        package_key,
        user_id=user_id,
        external_installation=external_installation,
        supplied_provenance=supplied_provenance,
        options=options,
    )


@celery_app.task(name=TASK_RECOVERY)
def recovery_sweep() -> dict[str, int]:
    return runs.recovery_sweep()


@celery_app.task(name=TASK_OPTIMISATION, acks_late=True)
def run_optimisation(study_id: str) -> str:
    from parallel_core.optimisation import run_study

    return run_study(study_id)


def _queues(consumer) -> set[str] | None:
    try:
        return {q.name for q in consumer.task_consumer.queues}
    except Exception:  # queue introspection is best effort; report every managed engine instead
        return None


@celery_app.task(name=TASK_ENGINE_STATUS, bind=True)
def report_engines(self) -> int:
    return engine_status.report_worker_engines(self.request.hostname)


@worker_ready.connect
def _report_on_start(sender=None, **_kw) -> None:
    """A worker states which managed engines it can run as soon as it starts."""
    engine_status.report_worker_engines(sender.hostname, _queues(sender))


@worker_shutdown.connect
def _forget_on_stop(sender=None, **_kw) -> None:
    engine_status.forget_worker(sender.hostname)
