"""Celery tasks for engine execution, external-result import and recovery."""

from __future__ import annotations

from parallel_core import runs
from parallel_core.jobs import (
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
