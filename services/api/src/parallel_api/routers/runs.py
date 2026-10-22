"""Simulation runs (/api/v1/runs): submission, status, progress events, cancellation, results."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from parallel_contracts.runs import TERMINAL_STATES, RunState
from parallel_core import events
from parallel_core.access import Action, require_project
from parallel_core.audit import record as audit
from parallel_core.db import SessionLocal, new_id
from parallel_core.models import Metric, RunArtifact, Scenario, ScenarioRevision, SimulationRun
from parallel_core.runs import request_cancel, submit_run, transition
from parallel_core.settings import get_settings
from parallel_core.storage import get_storage
from parallel_engines.base import sha256_file

from ..deps import DB, CurrentUser, Paging
from ..errors import ApiError
from ..schemas import ArtifactOut, MetricOut, PageOut, RunIn, RunOut, SignedUrlOut

router = APIRouter(tags=["runs"])


def _run(db, user, run_id: str, action=Action.view) -> SimulationRun:
    run = db.get(SimulationRun, run_id)
    if run is None:
        raise ApiError(404, "not_found", "Run not found")
    require_project(db, user, run.project_id, action)
    return run


@router.post("/projects/{project_id}/runs", response_model=RunOut, status_code=201)
def submit(
    project_id: str,
    body: RunIn,
    user: CurrentUser,
    db: DB,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    project = require_project(db, user, project_id, Action.edit)
    rev = db.get(ScenarioRevision, body.scenario_revision_id)
    if rev is None:
        raise ApiError(404, "not_found", "Scenario revision not found")
    sc = db.get(Scenario, rev.scenario_id)
    if sc is None or sc.project_id != project_id:
        raise ApiError(404, "not_found", "Scenario revision not found")
    result = submit_run(
        db,
        project=project,
        scenario_revision=rev,
        user_id=user.id,
        idempotency_key=idempotency_key or new_id(),
        execution_mode=body.execution_mode,
        label=body.label,
    )
    db.flush()
    return result.run


@router.get("/projects/{project_id}/runs", response_model=PageOut[RunOut])
def list_runs(
    project_id: str,
    user: CurrentUser,
    db: DB,
    page: Paging,
    scenario_id: str | None = None,
    state: str | None = None,
    engine_id: str | None = None,
):
    require_project(db, user, project_id)
    q = select(SimulationRun).where(SimulationRun.project_id == project_id)
    if scenario_id:
        q = q.where(SimulationRun.scenario_id == scenario_id)
    if state:
        q = q.where(SimulationRun.state == state)
    if engine_id:
        q = q.where(SimulationRun.engine_id == engine_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    items = db.scalars(
        q.order_by(SimulationRun.created_at.desc()).limit(page.limit).offset(page.offset)
    ).all()
    return PageOut(
        items=[RunOut.model_validate(r) for r in items], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, user: CurrentUser, db: DB):
    return _run(db, user, run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel(run_id: str, user: CurrentUser, db: DB):
    run = _run(db, user, run_id, Action.edit)
    request_cancel(db, run, user.id)
    return run


@router.get("/runs/{run_id}/metrics", response_model=list[MetricOut])
def metrics(run_id: str, user: CurrentUser, db: DB):
    _run(db, user, run_id)
    return db.scalars(
        select(Metric).where(Metric.run_id == run_id).order_by(Metric.metric_id, Metric.entity)
    ).all()


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactOut])
def artifacts(run_id: str, user: CurrentUser, db: DB, kind: str | None = None):
    _run(db, user, run_id)
    q = select(RunArtifact).where(RunArtifact.run_id == run_id)
    if kind:
        q = q.where(RunArtifact.kind == kind)
    return db.scalars(q.order_by(RunArtifact.kind, RunArtifact.name)).all()


@router.get("/runs/{run_id}/artifacts/{artifact_id}/url", response_model=SignedUrlOut)
def artifact_url(run_id: str, artifact_id: str, user: CurrentUser, db: DB):
    _run(db, user, run_id)
    a = db.get(RunArtifact, artifact_id)
    if a is None or a.run_id != run_id:
        raise ApiError(404, "not_found", "Artifact not found")
    return SignedUrlOut(
        url=get_storage().signed_url(a.storage_key, Path(a.name).name),
        expires_in_s=get_settings().signed_url_seconds,
    )


@router.get("/runs/{run_id}/data/{name}")
def artifact_json(run_id: str, name: str, user: CurrentUser, db: DB):
    """Small normalised JSON derivatives (playback, series, field) for the web viewer."""
    _run(db, user, run_id)
    if name not in (
        "playback.json",
        "series.json",
        "field.json",
        "evidence_fit.json",
        "result.json",
        "outcomes.json",
    ):
        raise ApiError(404, "not_found", "Unknown data file")
    a = db.scalar(
        select(RunArtifact)
        .where(RunArtifact.run_id == run_id, RunArtifact.name == name)
        .order_by(RunArtifact.created_at.desc())
    )
    if a is None:
        raise ApiError(404, "not_found", f"{name} not available for this run")
    if a.size_bytes > 200 * 1024 * 1024:
        raise ApiError(413, "too_large", "Use the signed artifact URL for large files")
    return json.loads(get_storage().get_bytes(a.storage_key))


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, user: CurrentUser, db: DB):
    """Server-sent events: state/progress snapshots until the run reaches a terminal state."""
    _run(db, user, run_id)

    def snapshot() -> dict:
        s = SessionLocal()
        try:
            r = s.get(SimulationRun, run_id)
            return {
                "run_id": run_id,
                "state": r.state,
                "progress": r.progress,
                "message": r.progress_message,
                "heartbeat_at": r.heartbeat_at.isoformat() if r.heartbeat_at else None,
                "error_message": r.error_message,
            }  # type: ignore[union-attr]
        finally:
            s.close()

    async def stream():
        ps = await run_in_threadpool(events.subscribe, "run", run_id)
        last = None
        try:
            while True:
                snap = await run_in_threadpool(snapshot)
                if snap != last:
                    yield f"event: run\ndata: {json.dumps(snap)}\n\n"
                    last = snap
                if RunState(snap["state"]) in TERMINAL_STATES:
                    break
                msg = await run_in_threadpool(ps.get_message, True, 2.0)
                if msg is None:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0)
        finally:
            await run_in_threadpool(ps.close)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- file exchange
@router.get("/runs/{run_id}/exchange-package", response_model=SignedUrlOut)
def exchange_package(run_id: str, user: CurrentUser, db: DB):
    run = _run(db, user, run_id, Action.edit)
    if not run.exchange_package_key:
        raise ApiError(404, "not_found", "This run has no exchange package")
    audit(
        db,
        action="run.exchange_package_downloaded",
        object_type="simulation_run",
        object_id=run.id,
        actor_id=user.id,
        project_id=run.project_id,
    )
    return SignedUrlOut(
        url=get_storage().signed_url(run.exchange_package_key, Path(run.exchange_package_key).name),
        expires_in_s=get_settings().signed_url_seconds,
    )


@router.post("/runs/{run_id}/external-result", response_model=RunOut)
def external_result(
    run_id: str,
    user: CurrentUser,
    db: DB,
    file: UploadFile = File(...),
    external_installation: str = Form(..., min_length=3),
    supplied_provenance: str = Form(..., min_length=3),
    options: str = Form("{}"),
):
    """Upload an operator-supplied result package for a run awaiting an external result."""
    run = _run(db, user, run_id, Action.edit)
    if run.state != RunState.awaiting_external_result.value:
        raise ApiError(409, "not_awaiting", f"Run is {run.state}, not awaiting an external result")
    try:
        opts = json.loads(options)
    except json.JSONDecodeError as exc:
        raise ApiError(422, "invalid_options", "options must be JSON") from exc
    s = get_settings()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "result.zip"
        size = 0
        with open(tmp, "wb") as out:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > s.max_upload_bytes:
                    raise ApiError(413, "too_large", "Result package too large")
                out.write(chunk)
        key = f"runs/{run_id}/external/{new_id()}-result.zip"
        get_storage().put_file(key, tmp, "application/zip")
        sha = sha256_file(tmp)
    transition(db, run, RunState.collecting, f"operator result uploaded (sha256 {sha[:12]}...)", by=user.id)
    audit(
        db,
        action="run.external_result_uploaded",
        object_type="simulation_run",
        object_id=run.id,
        actor_id=user.id,
        project_id=run.project_id,
        details={"sha256": sha, "external_installation": external_installation},
    )
    db.commit()
    if s.task_always_eager:
        from parallel_core.runs import import_external

        import_external(
            run.id,
            key,
            user_id=user.id,
            external_installation=external_installation,
            supplied_provenance=supplied_provenance,
            options=opts,
        )
    else:
        from parallel_core.jobs import TASK_IMPORT_RESULT, celery_app

        celery_app.send_task(
            TASK_IMPORT_RESULT, args=[run.id, key, user.id, external_installation, supplied_provenance, opts]
        )
    db.refresh(run)
    return run
