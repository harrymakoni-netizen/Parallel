"""SimPy adapter: discrete-event operations (gates, loading bays, crews, equipment)."""

from __future__ import annotations

import csv
import json

import numpy as np

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.operations import OperationsControls, OperationsModel
from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck, ResourceEstimate
from parallel_contracts.runs import ValidationIssue, ValidationReport
from parallel_contracts.scenario import ScenarioContent
from parallel_metrics.operations import EntityRecord, RequestRecord, check_conservation, operations_metrics

from ..base import (
    EngineAdapter,
    EngineError,
    ExecutionResult,
    NormalisedFile,
    NormalisedResult,
    PreparedInputs,
    RunContext,
    RunMonitor,
    crosscheck,
    hash_inputs,
    issue,
    now,
    write_json,
)
from ..capabilities import SIMPY
from ..runtime import ProcessLimits, pinned_requirements, probe_python_runtime, run_python_runner
from ..sampling import rng_for, sample


def _arrivals(flow, horizon: float, rng: np.random.Generator, scale: float) -> list[float]:
    a = flow.arrival
    end = min(a.end_s if a.end_s is not None else horizon, horizon)
    if a.kind == "schedule":
        times = sorted(t for t in (a.times_s or []) if t <= end)
    elif a.kind == "fixed_interval":
        times = list(np.arange(a.start_s, end + 1e-9, a.interval_s * scale))
    else:
        times, t = [], a.start_s
        while True:
            t += rng.exponential(a.mean_interarrival_s * scale)
            if t > end:
                break
            times.append(t)
    if a.max_count is not None:
        times = times[: a.max_count]
    return [float(x) for x in times]


class SimPyAdapter(EngineAdapter):
    capability = SIMPY

    def check_installation(self, mode: ExecutionMode | None = None) -> InstallationCheck:
        mode = mode or ExecutionMode.managed
        ok, out = probe_python_runtime("simpy", "import simpy; print(simpy.__version__)")
        if not ok:
            return self._check(
                mode,
                Availability.installation_required,
                details=[out],
                remedy="Run scripts/setup_runtimes.sh simpy on the simulation worker.",
            )
        if out != pinned_requirements("simpy").get("simpy"):
            return self._check(mode, Availability.unsupported_version, version=out)
        return self._check(mode, Availability.ready, version=out)

    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport:
        m = model.content
        if not isinstance(m, OperationsModel):
            return ValidationReport.from_issues(
                [issue("error", "wrong_model", "SimPy requires an operations model")]
            )
        issues = []
        for f in m.flows:
            for s in f.steps:
                if s.duration.unit not in ("s", "min", "h"):
                    issues.append(
                        issue("error", "duration_unit", f"Step {f.id}.{s.id} duration must be a time")
                    )
                if s.duration.kind == "normal" and s.duration.clip_low is None:
                    issues.append(
                        issue(
                            "warning",
                            "negative_durations",
                            f"Step {f.id}.{s.id}: normal durations without clip_low may be negative; "
                            "negative samples are set to zero.",
                        )
                    )
        return ValidationReport.from_issues(issues)

    def _validate_controls(
        self, model: DomainModelEnvelope, scenario: ScenarioContent
    ) -> list[ValidationIssue]:
        m, c = model.content, scenario.controls
        if not isinstance(m, OperationsModel) or not isinstance(c, OperationsControls):
            return [issue("error", "wrong_controls", "Operations scenario controls required")]
        rids = {r.id for r in m.resources} | {r.id for r in c.added_resources}
        out = [
            issue("error", "unknown_resource", f"Unknown resource {r}")
            for r in c.capacity_changes
            if r not in rids
        ]
        steps = {f"{f.id}.{s.id}" for f in m.flows for s in f.steps}
        for k, r in c.step_resource_changes.items():
            if k not in steps:
                out.append(issue("error", "unknown_step", f"Unknown step {k}"))
            if r not in rids:
                out.append(issue("error", "unknown_resource", f"Unknown resource {r}"))
        if scenario.settings.seed is None:
            out.append(issue("error", "seed_required", "Operations runs require a seed (stochastic inputs)"))
        return out

    def estimate_resources(self, model: DomainModelEnvelope, scenario: ScenarioContent) -> ResourceEstimate:
        return ResourceEstimate(
            cpu_cores=1,
            memory_mb=256,
            expected_runtime_s=5.0,
            basis="Small discrete-event models run in seconds",
        )

    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m, c = ctx.model.content, ctx.scenario.controls
        assert isinstance(m, OperationsModel) and isinstance(c, OperationsControls)
        notes = []
        capacities = {r.id: r.capacity for r in m.resources}
        for r in c.added_resources:
            capacities[r.id] = r.capacity
            notes.append(f"Added resource '{r.name}' ({r.id}) with capacity {r.capacity}.")
        for r, cap in c.capacity_changes.items():
            notes.append(f"Resource {r} capacity changed from {capacities[r]} to {cap}.")
            capacities[r] = cap
        for k, r in c.step_resource_changes.items():
            notes.append(f"Step {k} now uses resource {r}.")
        if c.arrival_interval_scale != 1.0:
            notes.append(f"Arrival intervals scaled by {c.arrival_interval_scale:g}.")
        reps = []
        for rep in range(c.replications):
            entities = []
            for f in m.flows:
                arrivals = _arrivals(
                    f, m.horizon_s, rng_for(ctx.seed, f"rep{rep}", f.id, "arrivals"), c.arrival_interval_scale
                )
                durs = {}
                for s in f.steps:
                    d = sample(s.duration, len(arrivals), rng_for(ctx.seed, f"rep{rep}", f.id, s.id), "s")
                    durs[s.id] = np.maximum(d, 0.0)
                for k, t in enumerate(arrivals):
                    entities.append(
                        {
                            "id": f"{f.id}-{k + 1:04d}",
                            "flow": f.id,
                            "arrival_s": t,
                            "steps": [
                                {
                                    "step": s.id,
                                    "resource": c.step_resource_changes.get(f"{f.id}.{s.id}", s.resource_id),
                                    "duration_s": float(durs[s.id][k]),
                                }
                                for s in f.steps
                            ],
                        }
                    )
            entities.sort(key=lambda e: (e["arrival_s"], e["id"]))
            reps.append({"replication": rep, "entities": entities})
        spec = {"capacities": capacities, "horizon_s": m.horizon_s, "replications": reps}
        d = ctx.workspace.input_dir
        files = {"runner_input.json": write_json(d / "runner_input.json", spec)}
        return PreparedInputs(
            files=files,
            hashes=hash_inputs(files),
            model_change_notes=notes,
            summary={
                "replications": c.replications,
                "horizon_s": m.horizon_s,
                "capacities": capacities,
                "arrivals_per_replication": [len(r["entities"]) for r in reps],
            },
        )

    def execute(self, ctx: RunContext, prepared: PreparedInputs, monitor: RunMonitor) -> ExecutionResult:
        started = now()
        log = ctx.workspace.log_dir / "simpy.log"
        out = run_python_runner(
            "simpy",
            "simpy_runner.py",
            cwd=ctx.workspace.root,
            args=[str(ctx.workspace.input_dir), str(ctx.workspace.output_dir)],
            log_path=log,
            monitor=monitor,
            limits=ProcessLimits(timeout_s=float(ctx.scenario.settings.timeout_s)),
        )
        rr = json.loads((ctx.workspace.output_dir / "runner_result.json").read_text())
        return ExecutionResult(
            engine_version=rr["engine_version"],
            runtime=rr,
            exit_code=out.exit_code,
            stdout_log=log,
            warnings=out.warnings,
            started_at=started,
            finished_at=now(),
        )

    def normalise_results(
        self, ctx: RunContext, prepared: PreparedInputs, execution: ExecutionResult
    ) -> NormalisedResult:
        m, c = ctx.model.content, ctx.scenario.controls
        assert isinstance(m, OperationsModel) and isinstance(c, OperationsControls)
        o = ctx.workspace.output_dir

        def f(v: str) -> float | None:
            return float(v) if v != "" else None

        reqs, ents = [], []
        with open(o / "events.csv") as fh:
            for r in csv.DictReader(fh):
                reqs.append(
                    RequestRecord(
                        int(r["replication"]),
                        r["entity"],
                        r["flow"],
                        r["step"],
                        r["resource"],
                        int(r["capacity"]),
                        float(r["request_s"]),
                        f(r["grant_s"]),
                        f(r["release_s"]),
                    )
                )
        with open(o / "entities.csv") as fh:
            for r in csv.DictReader(fh):
                ents.append(
                    EntityRecord(
                        int(r["replication"]), r["entity"], r["flow"], float(r["arrival_s"]), f(r["finish_s"])
                    )
                )
        try:
            check_conservation(reqs, m.horizon_s)
            conservation_ok = True
        except AssertionError as exc:
            raise EngineError(f"resource conservation check failed: {exc}") from exc
        caps = prepared.summary["capacities"]
        used = {r.resource for r in reqs} | {r.id for r in m.resources}
        metrics = operations_metrics(
            reqs,
            ents,
            m.horizon_s,
            c.replications,
            {k: v for k, v in caps.items() if k in used},
            [f.id for f in m.flows],
        )
        # Service completions must agree with the event records: an entity is complete iff every step's request was released.
        complete_by_events = 0
        by_ent: dict[tuple[int, str], list[RequestRecord]] = {}
        for r in reqs:
            by_ent.setdefault((r.replication, r.entity), []).append(r)
        steps_with_res = {
            fl.id: sum(1 for s in fl.steps if c.step_resource_changes.get(f"{fl.id}.{s.id}", s.resource_id))
            for fl in m.flows
        }
        for e in ents:
            rs = by_ent.get((e.replication, e.entity), [])
            if (
                len(rs) == steps_with_res[e.flow]
                and all(r.release_time is not None for r in rs)
                and e.finish_time is not None
            ):
                complete_by_events += 1
        complete_entities = sum(1 for e in ents if e.finish_time is not None)
        crosschecks = [
            crosscheck(
                "completed entities vs fully released request records",
                float(complete_entities),
                float(complete_by_events),
                0,
                0,
                "Completion must agree with the event log.",
            ),
            {
                "name": "resource conservation (busy units <= capacity; grants after requests)",
                "passed": conservation_ok,
                "normalised": None,
                "native": None,
                "abs_tolerance": 0,
                "rel_tolerance": 0,
                "tolerance_rationale": "Exact event-sweep check over all replications.",
            },
        ]
        series = {
            "queue_profiles": _queue_series(reqs, m.horizon_s, replication=0),
            "note": "Queue length over time for replication 0 (display only).",
        }
        (ctx.workspace.normalised_dir / "series.json").write_text(json.dumps(series))
        return NormalisedResult(
            metrics=metrics,
            crosschecks=crosschecks,
            files=[
                NormalisedFile(
                    "series.json",
                    ctx.workspace.normalised_dir / "series.json",
                    "timeseries",
                    "application/json",
                    "Queue length profiles (replication 0)",
                )
            ],
            summary=prepared.summary,
            units={"time": "s"},
        )


def _queue_series(
    reqs: list[RequestRecord], horizon: float, replication: int
) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for res in sorted({r.resource for r in reqs}):
        ev = []
        for r in reqs:
            if r.replication != replication or r.resource != res:
                continue
            end = min(r.grant_time if r.grant_time is not None else horizon, horizon)
            if end > r.request_time:
                ev += [(r.request_time, 1), (end, -1)]
        ev.sort()
        level, pts = 0, [[0.0, 0]]
        for t, d in ev:
            level += d
            pts.append([round(t, 1), level])
        out[res] = pts
    return out
