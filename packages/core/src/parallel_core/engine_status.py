"""Engine availability as the processing workers see it.

A simulation worker runs every managed engine's installation check when it starts and periodically,
and records the results here. The API host normally has no engines installed, so for managed runs
it trusts a recent report from a worker that serves the engine's queue. Connected and file-exchange
modes depend on the organisation's registered installations, not on a worker, and are checked where
they are requested. Reports from workers that stopped are ignored once they are older than
REPORT_MAX_AGE, so availability never outlives the worker that established it.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck

from .db import new_id, session_scope, utcnow
from .models import WorkerEngineReport

log = logging.getLogger("parallel.engine_status")

REPORT_INTERVAL_S = 300
REPORT_MAX_AGE = timedelta(seconds=3 * REPORT_INTERVAL_S)


def report_worker_engines(worker: str, queues: set[str] | None = None) -> int:
    """Check every managed engine this worker could run and record the results; returns the count."""
    from parallel_engines.registry import all_adapters

    from .jobs import queue_for_engine

    now = utcnow()
    rows = []
    for adapter in all_adapters():
        cap = adapter.capability
        if ExecutionMode.managed not in cap.execution_modes:
            continue
        if queues is not None and queue_for_engine(cap.engine_id) not in queues:
            continue
        try:
            chk = adapter.check_installation(ExecutionMode.managed)
        except Exception as e:  # a failing check is itself a result, never hidden
            chk = InstallationCheck(
                engine_id=cap.engine_id, execution_mode=ExecutionMode.managed,
                availability=Availability.integration_incomplete, checked_at=now,
                details=[f"installation check failed: {type(e).__name__}: {e}"],
            )
        chk.worker = worker
        rows.append({"worker": worker, "engine_id": cap.engine_id, "execution_mode": ExecutionMode.managed.value,
                     "availability": chk.availability.value, "check": chk.model_dump(mode="json"), "checked_at": now})
    if not rows:
        return 0
    with session_scope() as db:
        stmt = insert(WorkerEngineReport).values([{**r, "id": new_id()} for r in rows])
        db.execute(stmt.on_conflict_do_update(
            index_elements=["worker", "engine_id", "execution_mode"],
            set_={"availability": stmt.excluded.availability, "check": stmt.excluded.check,
                  "checked_at": stmt.excluded.checked_at},
        ))
    log.info("worker %s reported %d engine checks", worker, len(rows))
    return len(rows)


def forget_worker(worker: str) -> None:
    with session_scope() as db:
        db.execute(delete(WorkerEngineReport).where(WorkerEngineReport.worker == worker))


def worker_check(db, engine_id: str, mode: ExecutionMode) -> InstallationCheck | None:
    """The best recent worker report for an engine: a ready worker if any, otherwise the latest report."""
    reports = db.scalars(
        select(WorkerEngineReport)
        .where(WorkerEngineReport.engine_id == engine_id, WorkerEngineReport.execution_mode == mode.value,
               WorkerEngineReport.checked_at >= utcnow() - REPORT_MAX_AGE)
        .order_by(WorkerEngineReport.checked_at.desc())
    ).all()
    if not reports:
        return None
    best = next((r for r in reports if r.availability == Availability.ready.value), reports[0])
    return InstallationCheck.model_validate(best.check)

