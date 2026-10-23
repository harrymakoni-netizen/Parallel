"""Simulation run lifecycle (spec section 25).

* Idempotent submission per (project, idempotency key).
* Legal state transitions only, with a durable history.
* Worker heartbeats, cancellation, timeouts and bounded retries for
  non-deterministic failures; deterministic model errors are never retried.
* Recovery sweep re-queues (or fails) runs whose worker heartbeat was lost.
* Atomic publication: result files are uploaded under an attempt-specific
  prefix first; artifact/metric rows and the ``completed`` state are committed
  in a single transaction.
"""

from __future__ import annotations

import json
import mimetypes
import platform
import shutil
import socket
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.engines import Availability, ExecutionMode
from parallel_contracts.provenance import find_unadopted_ai_values
from parallel_contracts.runs import (
    ACTIVE_WORKER_STATES,
    TERMINAL_STATES,
    ArtifactRef,
    EngineMetadata,
    ImportedProvenance,
    ProcessingStep,
    ResultEnvelope,
    RevisionRefs,
    RunState,
    ValidationReport,
    check_transition,
)
from parallel_contracts.scenario import ScenarioContent
from parallel_engines.base import (
    EngineError,
    NormalisedResult,
    PreparedInputs,
    RunCancelled,
    RunContext,
    RunWorkspace,
    sha256_file,
)
from parallel_engines.registry import get_adapter

from . import events
from .audit import record as audit
from .db import session_scope, utcnow
from .models import (
    DomainModel,
    DomainModelRevision,
    EngineInstallation,
    Metric,
    Project,
    RunArtifact,
    Scenario,
    ScenarioRevision,
    SimulationRun,
    SourceAsset,
)
from .revisions import publish
from .settings import get_settings
from .storage import get_storage


class SubmissionError(ValueError):
    def __init__(self, message: str, *, status: int = 422, details: Any = None):
        super().__init__(message)
        self.status = status
        self.details = details


# ---------------------------------------------------------------- transitions
def transition(
    db: Session, run: SimulationRun, new: RunState, message: str | None = None, by: str | None = None
) -> None:
    current = RunState(run.state)
    check_transition(current, new)
    run.state = new.value
    now = utcnow()
    run.state_history = [
        *(run.state_history or []),
        {"state": new.value, "at": now.isoformat(), "by": by, "message": message},
    ]
    if new == RunState.queued:
        run.queued_at = now
    if new == RunState.preparing and run.started_at is None:
        run.started_at = now
    if new in TERMINAL_STATES:
        run.finished_at = now
    events.publish(
        "run", run.id, {"run_id": run.id, "state": new.value, "message": message, "progress": run.progress}
    )


def installation_for(db: Session, organisation_id: str, engine_id: str) -> dict | None:
    inst = db.scalar(
        select(EngineInstallation)
        .where(
            EngineInstallation.engine_id == engine_id, EngineInstallation.organisation_id == organisation_id
        )
        .order_by(EngineInstallation.created_at.desc())
    )
    if inst is None:
        return None
    return {
        "licence_confirmed": inst.licence_confirmed,
        "edition": inst.edition,
        "version": inst.version,
        "label": inst.label,
        "id": inst.id,
    }


def check_engine(db: Session, organisation_id: str, engine_id: str, mode: ExecutionMode):
    """Availability for a run: this host's own check, or, for managed runs this host cannot execute
    itself, a recent check reported by a worker that serves the engine's queue."""
    from .engine_status import worker_check

    adapter = get_adapter(engine_id)
    inst = installation_for(db, organisation_id, engine_id)
    try:
        local = adapter.check_installation(mode, installation=inst)  # type: ignore[call-arg]
    except TypeError:
        local = adapter.check_installation(mode)
    in_process = get_settings().task_always_eager  # eager execution runs here, so only this host counts
    if mode == ExecutionMode.managed and local.availability != Availability.ready and not in_process:
        remote = worker_check(db, engine_id, mode)
        if remote is not None:
            return remote
    return local


# ---------------------------------------------------------------- submission
@dataclass
class SubmitResult:
    run: SimulationRun
    created: bool


def submit_run(
    db: Session,
    *,
    project: Project,
    scenario_revision: ScenarioRevision,
    user_id: str,
    idempotency_key: str,
    execution_mode: str | None = None,
    label: str | None = None,
    optimisation_study_id: str | None = None,
    enqueue: bool = True,
) -> SubmitResult:
    existing = db.scalar(
        select(SimulationRun).where(
            SimulationRun.project_id == project.id, SimulationRun.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        return SubmitResult(existing, False)
    scenario = db.get(Scenario, scenario_revision.scenario_id)
    assert scenario is not None
    if scenario.project_id != project.id:
        raise SubmissionError("scenario does not belong to this project", status=404)
    if scenario_revision.origin == "ai" and scenario_revision.adopted_by is None:
        raise SubmissionError(
            "This scenario was drafted by the assistant and has not been adopted by a user.", status=409
        )
    dm = db.get(DomainModel, scenario.domain_model_id)
    if dm is None or dm.current_revision_id is None:
        raise SubmissionError("scenario's domain model has no revision")
    model_rev = db.get(DomainModelRevision, dm.current_revision_id)
    assert model_rev is not None
    sc_content = ScenarioContent.model_validate(scenario_revision.content)
    model = DomainModelEnvelope.model_validate(model_rev.content)
    unadopted = find_unadopted_ai_values(model_rev.content) + find_unadopted_ai_values(
        scenario_revision.content
    )
    if unadopted:
        raise SubmissionError(
            "Inputs contain AI suggestions that have not been adopted by a user.",
            status=409,
            details=unadopted[:20],
        )
    adapter = get_adapter(scenario.engine_id)
    mode = ExecutionMode(execution_mode) if execution_mode else adapter.default_mode()
    if mode not in adapter.capability.execution_modes:
        raise SubmissionError(
            f"{adapter.capability.name} does not support {mode.value} execution", status=422
        )
    check = check_engine(db, project.organisation_id, scenario.engine_id, mode)
    if check.availability != Availability.ready:
        raise SubmissionError(
            f"{adapter.capability.name} is not available: {check.availability.value}",
            status=409,
            details=check.model_dump(mode="json"),
        )
    s = get_settings()
    active = db.scalar(
        select(func.count())
        .select_from(SimulationRun)
        .join(Project)
        .where(
            Project.organisation_id == project.organisation_id,
            SimulationRun.state.in_([st.value for st in (RunState.queued, *ACTIVE_WORKER_STATES)]),
        )
    )
    if (active or 0) >= s.org_concurrent_run_limit:
        raise SubmissionError(
            f"Organisation concurrency limit reached ({s.org_concurrent_run_limit} active runs)", status=429
        )
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    this_month = db.scalar(
        select(func.count())
        .select_from(SimulationRun)
        .join(Project)
        .where(Project.organisation_id == project.organisation_id, SimulationRun.created_at >= month_start)
    )
    if (this_month or 0) >= s.org_monthly_run_quota:
        raise SubmissionError(
            f"Organisation monthly run quota reached ({s.org_monthly_run_quota} runs this month)", status=429
        )
    # Freeze the exact revisions the run will use.
    if scenario_revision.status != "published":
        publish(db, scenario_revision)
    if model_rev.status != "published":
        publish(db, model_rev)
    run = SimulationRun(
        project_id=project.id,
        case_id=scenario.case_id,
        scenario_id=scenario.id,
        scenario_revision_id=scenario_revision.id,
        domain_model_revision_id=model_rev.id,
        scene_revision_id=model.scene_revision_id,
        engine_id=scenario.engine_id,
        execution_mode=mode.value,
        state=RunState.draft.value,
        idempotency_key=idempotency_key,
        label=label,
        seed=sc_content.settings.seed,
        settings=sc_content.settings.model_dump(mode="json"),
        timeout_s=sc_content.settings.timeout_s,
        max_attempts=s.max_run_attempts,
        created_by=user_id,
        adapter_version=adapter.capability.adapter_version,
        optimisation_study_id=optimisation_study_id,
        state_history=[{"state": "draft", "at": utcnow().isoformat(), "by": user_id, "message": "submitted"}],
    )
    db.add(run)
    db.flush()
    transition(db, run, RunState.validating, by=user_id)
    report = adapter.validate_scenario(model, sc_content)
    run.validation = report.model_dump(mode="json")
    if not report.ok:
        run.error_code = "validation_failed"
        run.error_message = "; ".join(i.message for i in report.errors)
        transition(db, run, RunState.failed, "validation failed", by=user_id)
        audit(
            db,
            action="run.validation_failed",
            object_type="simulation_run",
            object_id=run.id,
            actor_id=user_id,
            organisation_id=project.organisation_id,
            project_id=project.id,
        )
        return SubmitResult(run, True)
    run.resource_estimate = adapter.estimate_resources(model, sc_content).model_dump(mode="json")
    transition(db, run, RunState.queued, by=user_id)
    audit(
        db,
        action="run.submitted",
        object_type="simulation_run",
        object_id=run.id,
        actor_id=user_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        details={"engine": run.engine_id, "mode": run.execution_mode},
    )
    if enqueue:
        db.flush()
        enqueue_run(db, run)
    return SubmitResult(run, True)


def enqueue_run(db: Session, run: SimulationRun, countdown: float = 0) -> None:
    from .jobs import TASK_EXECUTE_RUN, celery_app, queue_for_engine

    db.commit()  # the worker must see the queued run
    if get_settings().task_always_eager:
        # CLI/test mode: execute in this process (never used inside a web request handler)
        execute_run(run.id)
        db.refresh(run)
        return
    res = celery_app.send_task(
        TASK_EXECUTE_RUN, args=[run.id], queue=queue_for_engine(run.engine_id), countdown=countdown
    )
    run.celery_task_id = res.id
    db.commit()


def request_cancel(db: Session, run: SimulationRun, user_id: str) -> None:
    state = RunState(run.state)
    if state in TERMINAL_STATES:
        return
    if state in (RunState.draft, RunState.validating, RunState.queued, RunState.awaiting_external_result):
        transition(db, run, RunState.cancelled, "cancelled before execution", by=user_id)
    else:
        run.cancel_requested = True
        events.publish(
            "run", run.id, {"run_id": run.id, "state": run.state, "message": "cancellation requested"}
        )
    audit(
        db,
        action="run.cancel_requested",
        object_type="simulation_run",
        object_id=run.id,
        actor_id=user_id,
        project_id=run.project_id,
    )


# ---------------------------------------------------------------- worker side
class DbMonitor:
    """RunMonitor implementation persisting heartbeats/progress and polling the cancellation flag."""

    def __init__(self, run_id: str, min_interval_s: float = 2.0):
        self.run_id = run_id
        self.min_interval_s = min_interval_s
        self._last_beat = 0.0
        self._last_cancel_check = 0.0
        self._cancel = False
        self.lines: list[str] = []

    def heartbeat(self, progress: float | None = None, message: str | None = None) -> None:
        now = time.monotonic()
        if now - self._last_beat < self.min_interval_s and progress != 1.0:
            return
        self._last_beat = now
        with session_scope() as db:
            run = db.get(SimulationRun, self.run_id)
            if run is None:
                return
            run.heartbeat_at = utcnow()
            if progress is not None:
                run.progress = float(progress)
            if message:
                run.progress_message = message[:500]
            if run.cancel_requested:
                self._cancel = True
        events.publish("run", self.run_id, {"run_id": self.run_id, "progress": progress, "message": message})

    def cancel_requested(self) -> bool:
        now = time.monotonic()
        if not self._cancel and now - self._last_cancel_check >= 1.0:
            self._last_cancel_check = now
            with session_scope() as db:
                self._cancel = bool(
                    db.scalar(select(SimulationRun.cancel_requested).where(SimulationRun.id == self.run_id))
                )
        return self._cancel

    def log(self, line: str) -> None:
        self.lines.append(line)
        if len(self.lines) > 200:
            self.lines = self.lines[-200:]


def _set_state(run_id: str, new: RunState, message: str | None = None, **fields: Any) -> None:
    with session_scope() as db:
        run = db.get(SimulationRun, run_id, with_for_update=True)
        assert run is not None
        for k, v in fields.items():
            setattr(run, k, v)
        transition(db, run, new, message, by="worker")


def _context(db: Session, run: SimulationRun, ws: RunWorkspace) -> RunContext:
    model = DomainModelEnvelope.model_validate(
        db.get(DomainModelRevision, run.domain_model_revision_id).content
    )  # type: ignore[union-attr]
    scenario = ScenarioContent.model_validate(db.get(ScenarioRevision, run.scenario_revision_id).content)  # type: ignore[union-attr]
    storage = get_storage()
    asset_dir = ws.root / "assets"

    project_id = run.project_id

    def asset_path(asset_id: str) -> Path:
        # Own short-lived session: adapters may resolve assets after the caller's session has closed.
        with session_scope() as s2:
            asset = s2.get(SourceAsset, asset_id)
            if asset is None or asset.project_id != project_id:
                raise EngineError(f"asset {asset_id} not found in this project", deterministic=True)
            key, filename = asset.storage_key, Path(asset.filename).name
        dest = asset_dir / asset_id / filename
        if not dest.exists():
            storage.get_to_path(key, dest)
        return dest

    return RunContext(
        run_id=run.id, workspace=ws, model=model, scenario=scenario, seed=run.seed, asset_path=asset_path
    )


def _media_type(p: Path) -> str:
    if p.suffix == ".parquet":
        return "application/vnd.apache.parquet"
    return mimetypes.guess_type(p.name)[0] or "application/octet-stream"


def _upload(run_id: str, attempt: int, files: list[tuple[str, Path, str, str | None]]) -> list[ArtifactRef]:
    storage = get_storage()
    refs = []
    for name, path, kind, desc in files:
        key = f"runs/{run_id}/attempt-{attempt}/{kind}/{name}"
        mt = _media_type(path)
        storage.put_file(key, path, mt)
        refs.append(
            ArtifactRef(
                name=name,
                kind=kind,
                media_type=mt,
                sha256=sha256_file(path),
                size_bytes=path.stat().st_size,
                storage_key=key,
                description=desc,
            )
        )
    return refs


def execute_run(run_id: str) -> str:
    """Celery task body. Returns the final state (for logging)."""
    with session_scope() as db:
        run = db.get(SimulationRun, run_id, with_for_update=True)
        if run is None:
            return "missing"
        if run.state != RunState.queued.value:
            return f"skipped:{run.state}"  # duplicate delivery protection
        if run.cancel_requested:
            transition(db, run, RunState.cancelled, "cancelled before start", by="worker")
            return "cancelled"
        run.attempts += 1
        run.worker = f"{socket.gethostname()} ({platform.platform()})"
        run.heartbeat_at = utcnow()
        attempt = run.attempts
        transition(db, run, RunState.preparing, f"attempt {attempt}", by="worker")
    s = get_settings()
    ws = RunWorkspace(Path(s.workspace_root) / run_id / f"attempt-{attempt}")
    if ws.root.exists():
        shutil.rmtree(ws.root)
    ws.create()
    adapter = None
    history: list[ProcessingStep] = []
    monitor = DbMonitor(run_id)
    t0 = utcnow()
    try:
        with session_scope() as db:
            run = db.get(SimulationRun, run_id)
            assert run is not None
            ctx = _context(db, run, ws)
            adapter = get_adapter(run.engine_id)
            mode = ExecutionMode(run.execution_mode)
            prepared = adapter.prepare_inputs(ctx)
            input_refs = _upload(
                run_id, attempt, [(n, p, "input", "Engine input") for n, p in prepared.files.items()]
            )
            run.input_hashes = prepared.hashes
            run.model_change_notes = prepared.model_change_notes
            run.warnings = list(prepared.warnings)
            run.environment = {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "worker": socket.gethostname(),
            }
        history.append(
            ProcessingStep(stage="prepare_inputs", status="completed", started_at=t0, finished_at=utcnow())
        )
        if mode == ExecutionMode.file_exchange:
            pkg = adapter.build_exchange_package(ctx, prepared)
            refs = _upload(
                run_id, attempt, [(pkg.name, pkg, "package", "File-exchange input package for the operator")]
            )
            with session_scope() as db:
                run = db.get(SimulationRun, run_id, with_for_update=True)
                assert run is not None
                for r in [*input_refs, *refs]:
                    db.add(
                        RunArtifact(
                            run_id=run_id,
                            name=r.name,
                            kind=r.kind,
                            media_type=r.media_type,
                            sha256=r.sha256,
                            size_bytes=r.size_bytes,
                            storage_key=r.storage_key or "",
                            description=r.description,
                        )
                    )
                run.exchange_package_key = refs[0].storage_key
                run.result_summary = {"history": [h.model_dump(mode="json") for h in history]}
                transition(
                    db,
                    run,
                    RunState.awaiting_external_result,
                    "Package ready: waiting for an operator-supplied result (not automated execution).",
                    by="worker",
                )
            return RunState.awaiting_external_result.value
        _set_state(run_id, RunState.running)
        t1 = utcnow()
        execution = adapter.execute(ctx, prepared, monitor)
        history.append(
            ProcessingStep(stage="execute", status="completed", started_at=t1, finished_at=utcnow())
        )
        _set_state(run_id, RunState.collecting, engine_version=execution.engine_version)
        t2 = utcnow()
        raw = adapter.collect_raw_outputs(ctx)
        logs = sorted(p for p in ws.log_dir.glob("*") if p.is_file())
        raw_refs = _upload(
            run_id,
            attempt,
            [(str(p.relative_to(ws.output_dir)), p, "raw_output", None) for p in raw]
            + [(p.name, p, "log", "Engine log") for p in logs],
        )
        history.append(
            ProcessingStep(
                stage="collect_raw_outputs", status="completed", started_at=t2, finished_at=utcnow()
            )
        )
        _set_state(run_id, RunState.postprocessing)
        t3 = utcnow()
        result = adapter.normalise_results(ctx, prepared, execution)
        result.check_registered()
        history.append(
            ProcessingStep(stage="normalise_results", status="completed", started_at=t3, finished_at=utcnow())
        )
        _publish_result(
            run_id,
            attempt,
            ctx,
            prepared,
            result,
            input_refs + raw_refs,
            history,
            engine_version=execution.engine_version,
            runtime=execution.runtime,
            warnings=list(execution.warnings),
        )
        return RunState.completed.value
    except RunCancelled as exc:
        _finish_failure(run_id, RunState.cancelled, "cancelled", str(exc))
        return RunState.cancelled.value
    except EngineError as exc:
        return _handle_failure(run_id, exc.code, str(exc), retry=not exc.deterministic)
    except Exception as exc:  # unexpected adapter/infrastructure error: retry within the attempt budget
        return _handle_failure(run_id, "internal_error", f"{type(exc).__name__}: {exc}", retry=True)


def _finish_failure(run_id: str, state: RunState, code: str, message: str) -> None:
    with session_scope() as db:
        run = db.get(SimulationRun, run_id, with_for_update=True)
        assert run is not None
        if RunState(run.state) in TERMINAL_STATES:
            return
        run.error_code, run.error_message = code, message[:20000]
        transition(db, run, state, message[:300], by="worker")


def _handle_failure(run_id: str, code: str, message: str, retry: bool) -> str:
    with session_scope() as db:
        run = db.get(SimulationRun, run_id, with_for_update=True)
        assert run is not None
        if RunState(run.state) in TERMINAL_STATES:
            return run.state
        if retry and run.attempts < run.max_attempts and not run.cancel_requested:
            run.error_code, run.error_message = code, message[:20000]
            transition(db, run, RunState.queued, f"retrying after {code}", by="worker")
            enqueue_run(db, run, countdown=5)
            return "retrying"
        run.error_code, run.error_message = code, message[:20000]
        transition(db, run, RunState.failed, message[:300], by="worker")
        return RunState.failed.value


def _publish_result(
    run_id: str,
    attempt: int,
    ctx: RunContext,
    prepared: PreparedInputs,
    result: NormalisedResult,
    refs: list[ArtifactRef],
    history: list[ProcessingStep],
    *,
    engine_version: str | None,
    runtime: dict,
    warnings: list[str],
    imported: ImportedProvenance | None = None,
) -> None:
    norm_refs = _upload(run_id, attempt, [(f.name, f.path, f.kind, f.description) for f in result.files])
    with session_scope() as db:
        run = db.get(SimulationRun, run_id, with_for_update=True)
        assert run is not None
        if run.cancel_requested and imported is None:
            transition(db, run, RunState.cancelled, "cancelled during post-processing", by="worker")
            return
        all_refs = refs + norm_refs
        validation = (
            ValidationReport.model_validate(run.validation) if run.validation else ValidationReport(ok=True)
        )
        envelope = ResultEnvelope(
            run_id=run.id,
            project_id=run.project_id,
            revisions=RevisionRefs(
                scene_revision_id=run.scene_revision_id,
                domain_model_revision_id=run.domain_model_revision_id,
                scenario_revision_id=run.scenario_revision_id,
            ),
            engine=EngineMetadata(
                engine_id=run.engine_id,
                engine_version=engine_version,
                adapter_version=run.adapter_version or "",
                runtime=runtime,
            ),
            execution_mode=ExecutionMode(run.execution_mode),
            seed=run.seed,
            solver_settings=run.settings,
            environment=run.environment,
            input_hashes=prepared.hashes,
            output_hashes={r.name: r.sha256 for r in all_refs if r.kind not in ("input",)},
            metrics=result.metrics,
            trajectories=[r for r in norm_refs if r.kind == "trajectory"],
            timeseries=[r for r in norm_refs if r.kind == "timeseries"],
            fields=[r for r in norm_refs if r.kind == "field"],
            units=result.units,
            warnings=[*(run.warnings or []), *warnings, *result.warnings],
            validation=validation,
            native_crosschecks=result.crosschecks,
            processing_history=history,
            imported=imported,
            summary=result.summary,
        )
        env_bytes = envelope.model_dump_json(indent=2).encode()
        env_key = f"runs/{run_id}/attempt-{attempt}/result.json"
        get_storage().put_bytes(env_key, env_bytes, "application/json")
        import hashlib

        for r in all_refs:
            db.add(
                RunArtifact(
                    run_id=run_id,
                    name=r.name,
                    kind=r.kind,
                    media_type=r.media_type,
                    sha256=r.sha256,
                    size_bytes=r.size_bytes,
                    storage_key=r.storage_key or "",
                    description=r.description,
                )
            )
        db.add(
            RunArtifact(
                run_id=run_id,
                name="result.json",
                kind="normalised",
                media_type="application/json",
                sha256=hashlib.sha256(env_bytes).hexdigest(),
                size_bytes=len(env_bytes),
                storage_key=env_key,
                description="Result envelope (provenance, hashes, metrics, cross-checks)",
            )
        )
        for m in result.metrics:
            db.add(
                Metric(
                    run_id=run_id,
                    metric_id=m.metric_id,
                    entity=m.entity,
                    value=m.value,
                    unit=m.unit,
                    status=m.status.value,
                    note=m.note,
                    stats=m.stats,
                    replicate=m.replicate,
                )
            )
        run.engine_version = engine_version
        run.result_envelope_key = env_key
        run.warnings = envelope.warnings
        run.result_summary = {
            **result.summary,
            "crosschecks": result.crosschecks,
            "history": [h.model_dump(mode="json") for h in history],
        }
        run.progress = 1.0
        transition(db, run, RunState.completed, "results published", by="worker")


# ---------------------------------------------------------------- external results
def _package_metadata(extracted: Path) -> dict:
    """The operator's metadata.json from an exchange result package (untrusted; read as data only)."""
    p = extracted / "metadata.json"
    if not p.is_file() or p.stat().st_size > 256_000:
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str | int | float)} if isinstance(data, dict) else {}


def import_external(
    run_id: str,
    package_key: str,
    *,
    user_id: str,
    external_installation: str,
    supplied_provenance: str,
    options: dict,
) -> str:
    """Validate and normalise an operator-supplied result package for a file-exchange run."""
    from parallel_engines.exchange import ArchiveLimits, safe_extract

    s = get_settings()
    with session_scope() as db:
        run = db.get(SimulationRun, run_id, with_for_update=True)
        assert run is not None
        if run.state != RunState.collecting.value:
            return f"skipped:{run.state}"
        attempt = max(run.attempts, 1)
    ws = RunWorkspace(Path(s.workspace_root) / run_id / f"import-{int(time.time())}").create()
    pkg = ws.root / "result.zip"
    try:
        get_storage().get_to_path(package_key, pkg)
        package_sha = sha256_file(pkg)
        extracted = ws.root / "result"
        safe_extract(pkg, extracted, ArchiveLimits(max_total_bytes=s.max_archive_total_bytes))
        with session_scope() as db:
            run = db.get(SimulationRun, run_id)
            assert run is not None
            ctx = _context(db, run, ws)
            prepared = PreparedInputs(files={}, hashes=run.input_hashes or {})
        adapter = get_adapter(run.engine_id)
        result = adapter.import_external_result(ctx, extracted, options)
        result.check_registered()
        imported = ImportedProvenance(
            external_installation=external_installation,
            supplied_provenance=supplied_provenance,
            imported_by=user_id,
            imported_at=utcnow(),
            package_sha256=package_sha,
        )
        refs = _upload(
            run_id,
            attempt,
            [
                (str(p.relative_to(extracted)), p, "raw_output", "Operator-supplied result")
                for p in sorted(extracted.rglob("*"))
                if p.is_file()
            ],
        )
        with session_scope() as db:
            run = db.get(SimulationRun, run_id, with_for_update=True)
            assert run is not None
            transition(db, run, RunState.postprocessing, "imported result normalised", by=user_id)
        meta = result.summary.get("metadata") or _package_metadata(extracted)
        _publish_result(
            run_id,
            attempt,
            ctx,
            prepared,
            result,
            refs,
            [
                ProcessingStep(
                    stage="import_external_result",
                    status="completed",
                    started_at=utcnow(),
                    finished_at=utcnow(),
                    message=f"package sha256 {package_sha}",
                )
            ],
            engine_version=str(meta.get("software_version") or meta.get("hec_ras_version") or "not reported"),
            runtime={"external_installation": external_installation},
            warnings=[],
            imported=imported,
        )
        with session_scope() as db:
            run = db.get(SimulationRun, run_id)
            assert run is not None
            run.imported_by, run.imported_at, run.external_installation = (
                user_id,
                utcnow(),
                external_installation,
            )
        return RunState.completed.value
    except EngineError as exc:
        _finish_failure(run_id, RunState.failed, exc.code, f"Imported result rejected: {exc}")
        return RunState.failed.value


# ---------------------------------------------------------------- recovery
def recovery_sweep(now_fn=utcnow) -> dict[str, int]:
    """Re-queue or fail runs whose worker heartbeat has been lost."""
    s = get_settings()
    cutoff = now_fn() - timedelta(seconds=s.heartbeat_timeout_s)
    requeued = failed = 0
    with session_scope() as db:
        stale = db.scalars(
            select(SimulationRun)
            .where(
                SimulationRun.state.in_([st.value for st in ACTIVE_WORKER_STATES]),
                SimulationRun.heartbeat_at < cutoff,
            )
            .with_for_update(skip_locked=True)
        ).all()
        for run in stale:
            if run.attempts < run.max_attempts and not run.cancel_requested:
                run.error_code = "heartbeat_lost"
                transition(db, run, RunState.queued, "worker heartbeat lost; re-queued", by="recovery")
                requeued += 1
            else:
                run.error_code = "heartbeat_lost"
                run.error_message = "Worker heartbeat lost and the retry budget is exhausted."
                transition(
                    db,
                    run,
                    RunState.cancelled if run.cancel_requested else RunState.failed,
                    "worker heartbeat lost",
                    by="recovery",
                )
                failed += 1
        ids = [r.id for r in stale if r.state == RunState.queued.value]
    if ids:
        from .jobs import TASK_EXECUTE_RUN, celery_app

        with session_scope() as db:
            for rid in ids:
                run = db.get(SimulationRun, rid)
                assert run is not None
                from .jobs import queue_for_engine

                res = celery_app.send_task(
                    TASK_EXECUTE_RUN, args=[rid], queue=queue_for_engine(run.engine_id)
                )
                run.celery_task_id = res.id
    return {"requeued": requeued, "failed": failed}


def run_summary(run: SimulationRun) -> dict[str, Any]:
    return json.loads(json.dumps({"id": run.id, "state": run.state, "engine_id": run.engine_id}, default=str))
