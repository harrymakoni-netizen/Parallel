"""JuPedSim adapter: pedestrian movement and evacuation."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from shapely.geometry import Point

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.pedestrian import PedestrianControls, PedestrianModel
from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck, ResourceEstimate
from parallel_contracts.metrics import MetricStatus, MetricValue
from parallel_contracts.runs import ValidationIssue, ValidationReport
from parallel_contracts.scenario import ScenarioContent
from parallel_metrics.evacuation import (
    PersonOutcome,
    evacuated_curve,
    evacuation_metrics,
    slow_movement_fraction,
    travel_time_histogram,
)
from parallel_scene.geometry import (
    component_index,
    polygon_component_overlap,
    sample_points_in_polygon,
    to_shapely,
    validate_polygon,
    walkable_area,
)

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
from ..capabilities import JUPEDSIM
from ..runtime import ProcessLimits, pinned_requirements, probe_python_runtime, run_python_runner
from ..sampling import rng_for, sample

DENSITY_BAND_M = 2.0
MAX_START_DENSITY = 4.0  # persons per m², above which start zones are rejected


class JuPedSimAdapter(EngineAdapter):
    capability = JUPEDSIM

    def check_installation(self, mode: ExecutionMode | None = None) -> InstallationCheck:
        mode = mode or ExecutionMode.managed
        ok, out = probe_python_runtime("jupedsim", "import jupedsim, shapely; print(jupedsim.__version__)")
        expected = pinned_requirements("jupedsim").get("jupedsim", self.capability.pinned_version)
        if not ok:
            return self._check(
                mode,
                Availability.installation_required,
                details=[out],
                remedy="Run scripts/setup_runtimes.sh jupedsim on the simulation worker.",
            )
        if out != expected:
            return self._check(
                mode,
                Availability.unsupported_version,
                version=out,
                details=[f"pinned {expected}, found {out}"],
                remedy="Recreate the runtime from the pinned spec.",
            )
        return self._check(
            mode, Availability.ready, version=out, details=["isolated runtime .runtimes/jupedsim"]
        )

    # ---------------------------------------------------------------- validation
    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport:
        m = model.content
        if not isinstance(m, PedestrianModel):
            return ValidationReport.from_issues(
                [issue("error", "wrong_model", "JuPedSim requires a pedestrian_geometry model")]
            )
        issues: list[ValidationIssue] = []
        issues += validate_polygon(m.walkable_area, "content.walkable_area", "Walkable area")
        for i, o in enumerate(m.obstacles):
            issues += validate_polygon(o.polygon, f"content.obstacles[{i}]", f"Obstacle {o.id}")
        for i, e in enumerate(m.exits):
            issues += validate_polygon(e.polygon, f"content.exits[{i}]", f"Exit {e.id}")
        if any(i.severity == "error" for i in issues):
            return ValidationReport.from_issues(issues)
        base = walkable_area(m.walkable_area, [o.polygon for o in m.obstacles])
        if not base.components:
            issues.append(
                issue("error", "empty_walkable_area", "The walkable area is empty after excluding obstacles")
            )
            return ValidationReport.from_issues(issues)
        if len(base.components) > 1:
            issues.append(
                issue(
                    "warning",
                    "disconnected_geometry",
                    f"The walkable area has {len(base.components)} disconnected components; each is simulated as a separate "
                    "model and people without a reachable destination are counted as unreachable.",
                    path="content.walkable_area",
                )
            )
        for i, e in enumerate(m.exits):
            if not polygon_component_overlap(base.components, e.polygon):
                issues.append(
                    issue(
                        "error",
                        "exit_outside_walkable",
                        f"Exit {e.id} does not overlap the walkable area",
                        path=f"content.exits[{i}]",
                        hint="Move the exit polygon onto the walkable boundary.",
                    )
                )
        for i, s in enumerate(m.route_segments):
            if to_shapely(s.polygon).intersection(base.area).area <= 1e-6:
                issues.append(
                    issue(
                        "warning",
                        "segment_outside_walkable",
                        f"Route segment {s.id} does not overlap the walkable area",
                        path=f"content.route_segments[{i}]",
                    )
                )
        for i, g in enumerate(m.start_groups):
            zone = to_shapely(g.zone).intersection(base.area)
            if zone.area <= 1e-6 and g.count > 0:
                issues.append(
                    issue(
                        "error",
                        "start_zone_outside",
                        f"Start zone of group {g.id} is outside the walkable area",
                        path=f"content.start_groups[{i}]",
                    )
                )
            elif g.count / max(zone.area, 1e-9) > MAX_START_DENSITY:
                issues.append(
                    issue(
                        "error",
                        "start_density",
                        f"Group {g.id}: {g.count} people in {zone.area:.1f} m² exceeds "
                        f"{MAX_START_DENSITY} persons/m²",
                        path=f"content.start_groups[{i}]",
                    )
                )
            if g.positions:
                for p in g.positions:
                    if not base.area.buffer(1e-6).covers(Point(p[0], p[1])):
                        issues.append(
                            issue(
                                "error",
                                "start_outside",
                                f"Group {g.id}: start position {p} is outside the walkable area",
                                path=f"content.start_groups[{i}].positions",
                            )
                        )
                        break
        if not m.start_groups or sum(g.count for g in m.start_groups) == 0:
            issues.append(issue("warning", "no_population", "The model has no people in its start groups"))
        return ValidationReport.from_issues(issues)

    def _validate_controls(
        self, model: DomainModelEnvelope, scenario: ScenarioContent
    ) -> list[ValidationIssue]:
        m, c = model.content, scenario.controls
        if not isinstance(m, PedestrianModel) or not isinstance(c, PedestrianControls):
            return [issue("error", "wrong_controls", "Pedestrian scenario controls required")]
        out = []
        seg_ids, exit_ids, group_ids = (
            {s.id for s in m.route_segments},
            {e.id for e in m.exits},
            {g.id for g in m.start_groups},
        )
        for s in c.closed_route_segments:
            if s not in seg_ids:
                out.append(issue("error", "unknown_segment", f"Closed route segment {s} does not exist"))
        for e in c.closed_exits:
            if e not in exit_ids:
                out.append(issue("error", "unknown_exit", f"Closed exit {e} does not exist"))
        if exit_ids and set(c.closed_exits) >= exit_ids:
            out.append(
                issue(
                    "warning",
                    "all_exits_closed",
                    "Every exit is closed: everyone will be counted as unreachable",
                )
            )
        for g, exits in c.exit_assignment.items():
            if g not in group_ids:
                out.append(issue("error", "unknown_group", f"Exit assignment for unknown group {g}"))
            for e in exits:
                if e not in exit_ids:
                    out.append(issue("error", "unknown_exit", f"Exit assignment references unknown exit {e}"))
        for g in c.population_overrides:
            if g not in group_ids:
                out.append(issue("error", "unknown_group", f"Population override for unknown group {g}"))
        if scenario.settings.seed is None and any(g.positions is None for g in m.start_groups):
            out.append(
                issue("error", "seed_required", "A seed is required to generate start positions reproducibly")
            )
        return out

    def estimate_resources(self, model: DomainModelEnvelope, scenario: ScenarioContent) -> ResourceEstimate:
        m, c = model.content, scenario.controls
        assert isinstance(m, PedestrianModel) and isinstance(c, PedestrianControls)
        n = sum(c.population_overrides.get(g.id, g.count) for g in m.start_groups)
        steps = c.max_duration_s / c.dt_s
        # Reference workflow: ~1.5e-6 s per agent-step on a single core (upper-bound planning figure).
        est = 2.0 + 1.5e-6 * max(n, 1) * steps
        return ResourceEstimate(
            cpu_cores=1,
            memory_mb=512,
            expected_runtime_s=round(est, 1),
            basis=f"{n} agents x {steps:.0f} steps x 1.5 µs (measured on the reference workflow, upper bound)",
            within_limits=n <= 20000,
            limit_notes=[] if n <= 20000 else ["Above 20,000 agents"],
        )

    # ---------------------------------------------------------------- preparation
    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m, c = ctx.model.content, ctx.scenario.controls
        assert isinstance(m, PedestrianModel) and isinstance(c, PedestrianControls)
        notes: list[str] = []
        warnings: list[str] = []
        closed_segs = [s for s in m.route_segments if s.id in c.closed_route_segments]
        base = walkable_area(m.walkable_area, [o.polygon for o in m.obstacles])
        closed = walkable_area(
            m.walkable_area, [o.polygon for o in m.obstacles] + [s.polygon for s in closed_segs]
        )
        for s in closed_segs:
            notes.append(
                f"Route segment '{s.name}' ({s.id}) closed from t = 0: its polygon is added as an excluded area."
            )
        open_exits = [e for e in m.exits if e.id not in c.closed_exits]
        for e in m.exits:
            if e.id in c.closed_exits:
                notes.append(
                    f"Exit '{e.name}' ({e.id}) unavailable from t = 0: no exit stage is created for it."
                )
        comps = closed.components
        comp_exits: dict[int, list[dict]] = {i: [] for i in range(len(comps))}
        for e in open_exits:
            eg = to_shapely(e.polygon)
            for ci in polygon_component_overlap(comps, e.polygon):
                part = eg.intersection(comps[ci])
                inner = part.buffer(-0.01) if part.buffer(-0.01).area > 0 else part
                if inner.geom_type == "MultiPolygon":
                    inner = max(inner.geoms, key=lambda g: g.area)
                comp_exits[ci].append({"id": e.id, "polygon_wkt": inner.wkt})

        speed_dist = c.desired_speed_override or m.movement.desired_speed
        if c.desired_speed_override:
            notes.append("Desired speed distribution overridden by the scenario.")
        if c.alarm_delay_s:
            notes.append(f"Alarm delay {c.alarm_delay_s:.0f} s added to every person's movement start.")
        agents: list[dict] = []
        population: list[dict] = []
        for g in m.start_groups:
            n = c.population_overrides.get(g.id, g.count)
            if n != g.count:
                notes.append(f"Group '{g.name}' population changed from {g.count} to {n}.")
            if n == 0:
                continue
            if g.positions is not None and n == g.count:
                pts = [tuple(p[:2]) for p in g.positions]
            else:
                # Sample against the geometry *before* scenario closures so populations match across scenarios.
                zone = to_shapely(g.zone).intersection(base.area)
                if zone.is_empty:
                    raise EngineError(
                        f"start zone of group {g.id} is outside the walkable area", deterministic=True
                    )
                pts = sample_points_in_polygon(
                    zone, n, rng_for(ctx.seed, "positions", g.id), min_spacing=0.55
                )
            rng = rng_for(ctx.seed, "params", g.id)
            speeds = sample(speed_dist, n, rng, "m/s")
            radii = sample(m.movement.radius, n, rng, "m")
            premove = sample(c.pre_movement, n, rng, "s")
            permitted = c.exit_assignment.get(g.id, [e.id for e in m.exits])
            for k in range(n):
                aid = f"{g.id}-{k + 1:04d}"
                pos = pts[k]
                start = float(c.alarm_delay_s + max(0.0, premove[k]))
                rec = {
                    "id": aid,
                    "group": g.id,
                    "position": [float(pos[0]), float(pos[1])],
                    "desired_speed_mps": float(max(0.1, speeds[k])),
                    "radius_m": float(max(0.1, radii[k])),
                    "start_time_s": start,
                }
                ci = component_index(comps, pos)
                open_permitted = [e for e in permitted if e not in c.closed_exits]
                reachable = [e["id"] for e in comp_exits.get(ci, [])] if ci is not None else []
                usable = [e for e in open_permitted if e in reachable]
                if ci is None:
                    reason = "Start position lies inside an excluded (closed) area."
                elif not open_permitted:
                    reason = "All permitted destinations are closed."
                elif not usable:
                    reason = "No permitted open destination is reachable from the start position."
                else:
                    reason = None
                population.append(
                    {
                        **rec,
                        "component": ci,
                        "permitted_exits": open_permitted,
                        "status": "unreachable" if reason else "simulated",
                        "reason": reason,
                    }
                )
                if not reason:
                    agents.append({**rec, "component": ci, "permitted_exits": usable})
        unreachable = sum(1 for p in population if p["status"] == "unreachable")
        if unreachable:
            warnings.append(
                f"{unreachable} people cannot reach a permitted open destination; they are counted as unreachable."
            )
        if len(comps) > 1:
            notes.append(
                f"Walkable area has {len(comps)} disconnected components after closures; each is simulated separately."
            )
        runner_input = {
            "model": m.movement.model_dump(
                include={
                    "strength_neighbor_repulsion",
                    "range_neighbor_repulsion",
                    "strength_geometry_repulsion",
                    "range_geometry_repulsion",
                    "time_gap_s",
                }
            ),
            "dt_s": c.dt_s,
            "max_duration_s": c.max_duration_s,
            "trajectory_every_n_steps": c.trajectory_every_n_steps,
            "components": [
                {"id": i, "polygon_wkt": comps[i].wkt, "exits": comp_exits[i]} for i in range(len(comps))
            ],
            "agents": agents,
        }
        d = ctx.workspace.input_dir
        files = {
            "runner_input.json": write_json(d / "runner_input.json", runner_input),
            "population.json": write_json(d / "population.json", population),
            "model.json": write_json(d / "model.json", ctx.model.model_dump(mode="json")),
            "scenario.json": write_json(d / "scenario.json", ctx.scenario.model_dump(mode="json")),
        }
        positions_hash = hashlib.sha256(
            json.dumps([[p["id"], p["position"]] for p in population], sort_keys=True).encode()
        ).hexdigest()
        return PreparedInputs(
            files=files,
            hashes=hash_inputs(files),
            model_change_notes=notes,
            warnings=warnings,
            summary={
                "initial_population": len(population),
                "simulated": len(agents),
                "unreachable": unreachable,
                "components": len(comps),
                "open_exits": [e.id for e in open_exits],
                "population_positions_sha256": positions_hash,
            },
        )

    # ---------------------------------------------------------------- execution
    def execute(self, ctx: RunContext, prepared: PreparedInputs, monitor: RunMonitor) -> ExecutionResult:
        started = now()
        c = ctx.scenario.controls
        assert isinstance(c, PedestrianControls)
        limits = ProcessLimits(timeout_s=float(ctx.scenario.settings.timeout_s), cores=1)
        log = ctx.workspace.log_dir / "jupedsim.log"
        out = run_python_runner(
            "jupedsim",
            "jupedsim_runner.py",
            cwd=ctx.workspace.root,
            args=[str(ctx.workspace.input_dir), str(ctx.workspace.output_dir)],
            log_path=log,
            monitor=monitor,
            limits=limits,
            progress_base=0.05,
            progress_span=0.85,
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

    # ---------------------------------------------------------------- normalisation
    def normalise_results(
        self, ctx: RunContext, prepared: PreparedInputs, execution: ExecutionResult
    ) -> NormalisedResult:
        m, c = ctx.model.content, ctx.scenario.controls
        assert isinstance(m, PedestrianModel) and isinstance(c, PedestrianControls)
        population = json.loads(prepared.files["population.json"].read_text())
        by_id = {p["id"]: p for p in population}
        sim_rows = {}
        with open(ctx.workspace.output_dir / "agents.csv") as f:
            for r in csv.DictReader(f):
                sim_rows[r["agent"]] = r
        outcomes: list[PersonOutcome] = []
        for p in population:
            if p["status"] == "unreachable":
                outcomes.append(
                    PersonOutcome(
                        p["id"],
                        p["group"],
                        "unreachable",
                        p["start_time_s"],
                        None,
                        None,
                        p["desired_speed_mps"],
                        p["reason"],
                    )
                )
                continue
            r = sim_rows.get(p["id"])
            if r is None:
                raise EngineError(f"runner produced no record for simulated agent {p['id']}")
            if r["status"] == "evacuated":
                outcomes.append(
                    PersonOutcome(
                        p["id"],
                        p["group"],
                        "evacuated",
                        p["start_time_s"],
                        float(r["exit_time_s"]),
                        r["exit_id"],
                        p["desired_speed_mps"],
                    )
                )
            else:
                outcomes.append(
                    PersonOutcome(
                        p["id"],
                        p["group"],
                        "remaining",
                        p["start_time_s"],
                        None,
                        None,
                        p["desired_speed_mps"],
                    )
                )
        extra = set(sim_rows) - set(by_id)
        if extra:
            raise EngineError(f"runner reported unknown agents: {sorted(extra)[:5]}")
        open_exits = prepared.summary["open_exits"]
        metrics = evacuation_metrics(outcomes, len(population), open_exits)

        # trajectories -> parquet (original precision) + decimated browser derivative
        tpath = ctx.workspace.output_dir / "trajectories.csv"
        tbl = _read_traj(tpath)
        norm = ctx.workspace.normalised_dir
        files: list[NormalisedFile] = []
        pq.write_table(tbl, norm / "trajectories.parquet", compression="zstd")
        files.append(
            NormalisedFile(
                "trajectories.parquet",
                norm / "trajectories.parquet",
                "trajectory",
                "application/vnd.apache.parquet",
                "Agent positions every n steps (m, s)",
            )
        )
        playback = _playback(tbl, min_dt=0.5)
        (norm / "playback.json").write_text(json.dumps(playback, separators=(",", ":")))
        files.append(
            NormalisedFile(
                "playback.json",
                norm / "playback.json",
                "trajectory",
                "application/json",
                "Browser playback derivative (≥0.5 s frames, cm precision); metrics use the original",
            )
        )

        warnings: list[str] = list(prepared.warnings)
        crosschecks = []
        # Congestion indicators from trajectories
        t_arr = tbl.column("t_s").to_numpy()
        a_arr = tbl.column("agent").to_numpy(zero_copy_only=False).astype(str)
        x_arr, y_arr = tbl.column("x").to_numpy(), tbl.column("y").to_numpy()
        closed_area = walkable_area(
            m.walkable_area,
            [o.polygon for o in m.obstacles]
            + [s.polygon for s in m.route_segments if s.id in c.closed_route_segments],
        ).area
        if len(t_arr):
            pts = shapely.points(x_arr, y_arr)
            outside = int(np.sum(~shapely.covers(closed_area.buffer(0.05), pts)))
            crosschecks.append(
                crosscheck(
                    "trajectory samples outside prepared walkable geometry",
                    float(outside),
                    0.0,
                    0.0,
                    0.0,
                    "Every recorded position must lie in the prepared walkable area (5 cm tolerance).",
                )
            )
            frame_idx = np.unique(t_arr, return_inverse=True)[1]
            n_frames = int(frame_idx.max()) + 1
            for e in m.exits:
                if e.id not in open_exits:
                    continue
                eg = to_shapely(e.polygon)
                band_area = eg.buffer(DENSITY_BAND_M).intersection(closed_area).area
                near = shapely.distance(eg, pts) <= DENSITY_BAND_M
                counts = np.bincount(frame_idx[near], minlength=n_frames) if band_area > 0 else np.array([0])
                metrics.append(
                    MetricValue(
                        metric_id="evac.peak_exit_density",
                        value=float(counts.max() / band_area) if band_area > 0 else None,
                        unit="1/m2",
                        entity=e.id,
                        status=MetricStatus.available if band_area > 0 else MetricStatus.unavailable,
                    )
                )
            smf = slow_movement_fraction(
                a_arr,
                t_arr,
                x_arr,
                y_arr,
                {p["id"]: p["desired_speed_mps"] for p in population},
                {p["id"]: p["start_time_s"] for p in population},
            )
            metrics.append(
                MetricValue(
                    metric_id="evac.slow_movement_fraction",
                    value=smf,
                    unit="1",
                    status=MetricStatus.available if smf is not None else MetricStatus.unavailable,
                )
            )
        else:
            metrics.append(
                MetricValue(
                    metric_id="evac.slow_movement_fraction",
                    value=None,
                    unit="1",
                    status=MetricStatus.unavailable,
                    note="No trajectory frames recorded.",
                )
            )
        runner_evac = sum(1 for r in sim_rows.values() if r["status"] == "evacuated")
        norm_evac = next(mv.value for mv in metrics if mv.metric_id == "evac.evacuated")
        crosschecks.append(
            crosscheck(
                "evacuated count: normalised vs runner exit records",
                norm_evac,
                float(runner_evac),
                0,
                0,
                "Counts must agree exactly.",
            )
        )
        simulated = sum(cf["agents"] for cf in execution.runtime.get("components", []))
        crosschecks.append(
            crosscheck(
                "simulated agents: runner vs prepared",
                float(simulated),
                float(prepared.summary["simulated"]),
                0,
                0,
                "Every reachable person must be simulated.",
            )
        )
        curve = evacuated_curve(
            [o.exit_time_s for o in outcomes if o.exit_time_s is not None], c.max_duration_s, 5.0
        )
        series = {
            "evacuated_over_time": [
                {"t_s": t, "evacuated": n, "remaining_inside": prepared.summary["simulated"] - n}
                for t, n in curve
            ],
            "travel_time_histogram": travel_time_histogram(outcomes),
        }
        (norm / "series.json").write_text(json.dumps(series))
        files.append(
            NormalisedFile(
                "series.json",
                norm / "series.json",
                "timeseries",
                "application/json",
                "Evacuated people over time (5 s grid) and travel-time histogram",
            )
        )
        outcome_rows = [
            {
                "agent": o.agent_id,
                "group": o.group_id,
                "status": o.status,
                "exit_id": o.exit_id,
                "exit_time_s": o.exit_time_s,
                "start_time_s": o.start_time_s,
                "reason": o.reason,
            }
            for o in outcomes
        ]
        (norm / "outcomes.json").write_text(json.dumps(outcome_rows))
        files.append(
            NormalisedFile(
                "outcomes.json",
                norm / "outcomes.json",
                "normalised",
                "application/json",
                "Per-person outcome records (status categories are mutually exclusive)",
            )
        )
        if any(not cc["passed"] for cc in crosschecks):
            warnings.append("One or more native cross-checks failed; see validation summary.")
        return NormalisedResult(
            metrics=metrics,
            files=files,
            warnings=warnings,
            crosschecks=crosschecks,
            summary={**prepared.summary, "horizon_s": c.max_duration_s, "dt_s": c.dt_s},
            units={"time": "s", "length": "m", "density": "1/m2"},
        )


def _read_traj(path: Path) -> pa.Table:
    import pyarrow.csv as pacsv

    if not path.exists() or path.stat().st_size == 0:
        return pa.table(
            {
                "component": pa.array([], pa.int32()),
                "frame": pa.array([], pa.int32()),
                "t_s": pa.array([], pa.float64()),
                "agent": pa.array([], pa.string()),
                "x": pa.array([], pa.float64()),
                "y": pa.array([], pa.float64()),
            }
        )
    return pacsv.read_csv(
        path,
        convert_options=pacsv.ConvertOptions(
            column_types={
                "component": pa.int32(),
                "frame": pa.int32(),
                "t_s": pa.float64(),
                "agent": pa.string(),
                "x": pa.float64(),
                "y": pa.float64(),
            }
        ),
    )


def _playback(tbl: pa.Table, min_dt: float) -> dict:
    t = tbl.column("t_s").to_numpy()
    if not len(t):
        return {"frames": [], "agents": []}
    a = tbl.column("agent").to_numpy(zero_copy_only=False).astype(str)
    x, y = tbl.column("x").to_numpy(), tbl.column("y").to_numpy()
    agents = sorted(set(a))
    idx = {k: i for i, k in enumerate(agents)}
    frames = []
    last = -1e9
    for ft in np.unique(t):
        if ft - last < min_dt - 1e-9:
            continue
        last = ft
        sel = t == ft
        frames.append(
            {
                "t": round(float(ft), 2),
                "i": [idx[k] for k in a[sel]],
                "xy": [round(float(v), 2) for pair in zip(x[sel], y[sel], strict=True) for v in pair],
            }
        )
    return {
        "agents": agents,
        "frames": frames,
        "note": "Derived for display; metrics are computed from trajectories.parquet.",
    }
