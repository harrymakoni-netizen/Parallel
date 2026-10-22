"""Real Celery dispatch through Redis with an embedded worker: execution, cancellation, recovery."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest

from parallel_core import testing


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    url = testing.create_test_database()
    testing.configure_environment(url, tmp_path_factory.mktemp("worker"), eager=False)
    from parallel_core import jobs

    jobs.celery_app.conf.task_always_eager = False
    yield url
    testing.reset_caches()
    testing.drop_test_database(url)


@pytest.fixture(scope="module")
def worker(env):
    from celery.contrib.testing.worker import start_worker

    from parallel_core.jobs import celery_app
    from parallel_simulation import tasks  # noqa: F401

    with start_worker(
        celery_app, pool="solo", perform_ping_check=False, queues=["cpu", "maintenance"], shutdown_timeout=30
    ):
        yield


def _setup(model, scenario):
    from parallel_core.db import session_scope
    from parallel_core.models import (
        DomainModel,
        Organisation,
        OrganisationMembership,
        Project,
        Scenario,
        User,
    )
    from parallel_core.revisions import create_model_revision, create_scenario_revision

    with session_scope() as db:
        org = Organisation(name="W", slug=f"w-{time.time_ns()}", settings={})
        user = User(email=f"w{time.time_ns()}@example.test", display_name="W")
        db.add_all([org, user])
        db.flush()
        db.add(OrganisationMembership(organisation_id=org.id, user_id=user.id, role="admin"))
        p = Project(organisation_id=org.id, name="W", project_type="general")
        db.add(p)
        db.flush()
        dm = DomainModel(project_id=p.id, name="m", discipline=model.discipline.value)
        db.add(dm)
        db.flush()
        create_model_revision(db, dm, model, user.id, {"ok": True})
        sc = Scenario(project_id=p.id, domain_model_id=dm.id, name="s", engine_id=scenario.engine_id)
        db.add(sc)
        db.flush()
        rev = create_scenario_revision(db, sc, scenario, user.id)
        return p.id, rev.id, user.id


def _submit(project_id, rev_id, user_id, key):
    from parallel_core.db import session_scope
    from parallel_core.models import Project, ScenarioRevision
    from parallel_core.runs import submit_run

    with session_scope() as db:
        res = submit_run(
            db,
            project=db.get(Project, project_id),
            scenario_revision=db.get(ScenarioRevision, rev_id),
            user_id=user_id,
            idempotency_key=key,
        )
        return res.run.id


def _wait(run_id, states, timeout=180):
    from parallel_core.db import session_scope
    from parallel_core.models import SimulationRun

    t0 = time.time()
    while time.time() - t0 < timeout:
        with session_scope() as db:
            r = db.get(SimulationRun, run_id)
            if r.state in states:
                return r.state, r.attempts, [h["state"] for h in r.state_history], r.error_code
        time.sleep(0.5)
    raise TimeoutError(run_id)


def test_recovery_requeues_lost_heartbeat_then_fails_when_budget_exhausted(env):
    # Runs before the embedded worker starts so the re-queued message cannot race this test;
    # when the worker later receives it, the duplicate-delivery guard skips the (failed) run.
    from parallel_contracts.runs import RunState
    from parallel_core.db import session_scope, utcnow
    from parallel_core.models import SimulationRun
    from parallel_core.runs import recovery_sweep, transition
    from parallel_engines import reference_models as rm

    pid, rev, uid = _setup(rm.site_operations_model(), rm.operations_scenario())
    from parallel_core.db import session_scope as ss
    from parallel_core.models import Project, ScenarioRevision
    from parallel_core.runs import submit_run

    with ss() as db:
        run = submit_run(
            db,
            project=db.get(Project, pid),
            scenario_revision=db.get(ScenarioRevision, rev),
            user_id=uid,
            idempotency_key="k3",
            enqueue=False,
        ).run
        run_id = run.id
        # simulate a worker that started and then died
        run.attempts = 1
        transition(db, run, RunState.preparing, by="test")
        transition(db, run, RunState.running, by="test")
        run.heartbeat_at = utcnow() - timedelta(minutes=30)
    res = recovery_sweep()
    assert res["requeued"] == 1
    with session_scope() as db:
        r = db.get(SimulationRun, run_id)
        assert r.state == "queued" and r.error_code == "heartbeat_lost"
        # second loss with the budget exhausted -> failed
        r.attempts = r.max_attempts
        transition(db, r, RunState.preparing, by="test")
        r.heartbeat_at = utcnow() - timedelta(minutes=30)
    res = recovery_sweep()
    assert res["failed"] == 1
    with session_scope() as db:
        assert db.get(SimulationRun, run_id).state == "failed"


def test_dispatch_through_broker_executes_run(worker):
    from parallel_engines import reference_models as rm

    pid, rev, uid = _setup(rm.site_operations_model(), rm.operations_scenario())
    run_id = _submit(pid, rev, uid, "k1")
    state, attempts, hist, _ = _wait(run_id, {"completed", "failed"})
    assert state == "completed" and attempts == 1
    assert hist[-4:] == ["running", "collecting", "postprocessing", "completed"]


def test_cancel_running_job_kills_engine(worker):
    from parallel_core.db import session_scope
    from parallel_core.models import SimulationRun, User
    from parallel_core.runs import request_cancel
    from parallel_engines import reference_models as rm

    # a long CFD run (many iterations) that we cancel while it is running
    pid, rev, uid = _setup(rm.site_wind_model(), rm.cfd_scenario(iterations=5000))
    run_id = _submit(pid, rev, uid, "k2")
    _wait(run_id, {"running"})
    time.sleep(2)
    with session_scope() as db:
        request_cancel(db, db.get(SimulationRun, run_id), db.get(User, uid).id)
    state, _, hist, _ = _wait(run_id, {"cancelled", "completed", "failed"}, timeout=60)
    assert state == "cancelled", hist
