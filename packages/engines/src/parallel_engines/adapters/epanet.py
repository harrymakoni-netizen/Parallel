"""EPA EPANET adapter (via WNTR): pressurised water distribution networks."""

from __future__ import annotations

import csv
import json
from collections import defaultdict

import numpy as np

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.water_supply import WaterNetworkModel, WaterSupplyControls
from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck
from parallel_contracts.metrics import MetricValue
from parallel_contracts.runs import ValidationIssue, ValidationReport
from parallel_contracts.scenario import ScenarioContent
from parallel_scene.networks import Graph

from ..base import (
    EngineAdapter,
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
from ..capabilities import EPANET
from ..runtime import ProcessLimits, pinned_requirements, probe_python_runtime, run_python_runner


class EpanetAdapter(EngineAdapter):
    capability = EPANET

    def check_installation(self, mode: ExecutionMode | None = None) -> InstallationCheck:
        mode = mode or ExecutionMode.managed
        ok, out = probe_python_runtime(
            "epanet", "import wntr; from wntr.epanet import toolkit; print(wntr.__version__)"
        )
        if not ok:
            return self._check(
                mode,
                Availability.installation_required,
                details=[out],
                remedy="Run scripts/setup_runtimes.sh epanet on the simulation worker.",
            )
        if out != pinned_requirements("epanet").get("wntr"):
            return self._check(mode, Availability.unsupported_version, version=f"wntr {out}")
        return self._check(mode, Availability.ready, version=f"EPANET 2.2 via wntr {out}")

    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport:
        m = model.content
        if not isinstance(m, WaterNetworkModel):
            return ValidationReport.from_issues(
                [issue("error", "wrong_model", "EPANET requires a water_supply model")]
            )
        return ValidationReport.from_issues(_issues(m, set()))

    def _validate_controls(
        self, model: DomainModelEnvelope, scenario: ScenarioContent
    ) -> list[ValidationIssue]:
        m, c = model.content, scenario.controls
        if not isinstance(m, WaterNetworkModel) or not isinstance(c, WaterSupplyControls):
            return [issue("error", "wrong_controls", "Water supply scenario controls required")]
        pipes, juncs = {p.id for p in m.pipes}, {j.id for j in m.junctions}
        out = [
            issue("error", "unknown_pipe", f"Unknown pipe {p}")
            for p in [*c.closed_pipes, *c.pipe_diameter_changes_mm]
            if p not in pipes
        ]
        out += [
            issue("error", "unknown_junction", f"Unknown junction {j}")
            for j in c.extra_demand_lps
            if j not in juncs
        ]
        if not out and c.closed_pipes:
            out += [i for i in _issues(m, set(c.closed_pipes)) if i.code == "isolated_demand"]
        return out

    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m, c = ctx.model.content, ctx.scenario.controls
        assert isinstance(m, WaterNetworkModel) and isinstance(c, WaterSupplyControls)
        notes = []
        if c.demand_multiplier != 1:
            notes.append(f"All junction base demands multiplied by {c.demand_multiplier:g}.")
        for j, q in c.extra_demand_lps.items():
            notes.append(
                f"Junction {j}: additional constant demand of {q:g} L/s (no demand pattern applied)."
            )
        for p in c.closed_pipes:
            notes.append(f"Pipe {p} closed.")
        for p, dmm in c.pipe_diameter_changes_mm.items():
            notes.append(f"Pipe {p} diameter set to {dmm:g} mm.")
        spec = {
            "duration_s": m.duration_s,
            "hydraulic_step_s": m.hydraulic_step_s,
            "pattern_step_s": m.pattern_step_s,
            "report_step_s": m.report_step_s,
            "patterns": [{"id": p.id, "multipliers": p.multipliers} for p in m.patterns],
            "junctions": [
                {
                    "id": j.id,
                    "position": j.position,
                    "elevation_m": j.elevation_m,
                    "pattern_id": j.pattern_id,
                    "base_demand_m3s": j.base_demand_lps * c.demand_multiplier / 1000,
                    "extra_demand_m3s": c.extra_demand_lps.get(j.id, 0.0) / 1000,
                }
                for j in m.junctions
            ],
            "reservoirs": [r.model_dump(include={"id", "position", "head_m"}) for r in m.reservoirs],
            "tanks": [t.model_dump(exclude={"provenance"}) for t in m.tanks],
            "pipes": [
                {
                    "id": p.id,
                    "node1": p.node1,
                    "node2": p.node2,
                    "length_m": p.length_m,
                    "diameter_m": c.pipe_diameter_changes_mm.get(p.id, p.diameter_mm) / 1000,
                    "roughness_hw": p.roughness_hw,
                    "minor_loss": p.minor_loss,
                    "status": "CLOSED" if p.id in c.closed_pipes else p.status,
                }
                for p in m.pipes
            ],
            "pumps": [
                {
                    "id": p.id,
                    "node1": p.node1,
                    "node2": p.node2,
                    "speed": p.speed,
                    "curve_m3s_m": [[q / 1000, h] for q, h in p.curve_points],
                }
                for p in m.pumps
            ],
        }
        d = ctx.workspace.input_dir
        files = {"runner_input.json": write_json(d / "runner_input.json", spec)}
        return PreparedInputs(
            files=files,
            hashes=hash_inputs(files),
            model_change_notes=notes,
            summary={"min_pressure_threshold_m": c.min_pressure_m, "duration_s": m.duration_s},
        )

    def execute(self, ctx: RunContext, prepared: PreparedInputs, monitor: RunMonitor) -> ExecutionResult:
        started = now()
        log = ctx.workspace.log_dir / "epanet.log"
        out = run_python_runner(
            "epanet",
            "epanet_runner.py",
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
        assert isinstance(m, WaterNetworkModel) and isinstance(c, WaterSupplyControls)
        o = ctx.workspace.output_dir
        nodes: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
        with open(o / "nodes.csv") as f:
            for r in csv.DictReader(f):
                nodes[r["node"]].append((float(r["t_s"]), float(r["pressure_m"]), float(r["demand_m3s"])))
        links: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
        with open(o / "links.csv") as f:
            for r in csv.DictReader(f):
                links[r["link"]].append((float(r["t_s"]), float(r["flow_m3s"]), float(r["velocity_ms"])))
        metrics: list[MetricValue] = []
        jids = [j.id for j in m.junctions]
        mins = {j: min(p for _, p, _ in nodes[j]) for j in jids}
        for j in jids:
            metrics.append(MetricValue(metric_id="water.min_pressure", value=mins[j], unit="m", entity=j))
        metrics.append(
            MetricValue(metric_id="water.network_min_pressure", value=min(mins.values()), unit="m")
        )
        deficient = sum(1 for v in mins.values() if v < c.min_pressure_m)
        metrics.append(
            MetricValue(
                metric_id="water.deficient_junctions",
                value=deficient,
                unit="count",
                note=f"threshold {c.min_pressure_m:g} m",
            )
        )
        for p in m.pipes:
            metrics.append(
                MetricValue(
                    metric_id="water.max_velocity",
                    value=max(abs(v) for _, _, v in links[p.id]),
                    unit="m/s",
                    entity=p.id,
                )
            )
        step = m.report_step_s
        total = 0.0
        for j in jids:
            ser = nodes[j]
            total += sum(q * step for t, _, q in ser if t < m.duration_s)  # left Riemann over report steps
        metrics.append(MetricValue(metric_id="water.total_demand", value=total, unit="m3"))
        for t in m.tanks:
            lv = [p for _, p, _ in nodes[t.id]]
            metrics.append(
                MetricValue(
                    metric_id="water.tank_level_range", value=max(lv) - min(lv), unit="m", entity=t.id
                )
            )
        # Mass balance at each report time: junction demands are supplied by reservoirs/tanks (negative demand).
        balance: dict[float, float] = defaultdict(float)
        for ser in nodes.values():
            for t, _, q in ser:
                balance[t] += q
        times = sorted(balance)
        worst = max((abs(v) for v in balance.values()), default=0.0)
        crosschecks = [
            crosscheck(
                "network mass balance (sum of nodal demands incl. sources) at each report time",
                worst,
                0.0,
                abs_tol=1e-4,
                rel_tol=0.0,
                why="Demand-driven EPANET solutions conserve mass to solver tolerance (1e-4 m³/s).",
            )
        ]
        warnings = []
        neg = [j for j, v in mins.items() if v < 0]
        if neg:
            warnings.append(
                f"Negative pressures at {len(neg)} junction(s): demand-driven results are not physical there."
            )
        series = {
            "pressure": {j: [[t, round(p, 3)] for t, p, _ in nodes[j]] for j in jids},
            "flow": {lid: [[t, round(q * 1000, 3)] for t, q, _ in ser] for lid, ser in links.items()},
            "units": {"pressure": "m", "flow": "L/s"},
        }
        (ctx.workspace.normalised_dir / "series.json").write_text(json.dumps(series, separators=(",", ":")))
        return NormalisedResult(
            metrics=metrics,
            warnings=warnings,
            crosschecks=crosschecks,
            files=[
                NormalisedFile(
                    "series.json",
                    ctx.workspace.normalised_dir / "series.json",
                    "timeseries",
                    "application/json",
                    "Junction pressures and pipe flows",
                )
            ],
            summary={
                **prepared.summary,
                "report_steps": len(times),
                "max_mass_balance_error_m3s": float(np.round(worst, 9)),
            },
            units={"pressure": "m", "flow": "m3/s", "velocity": "m/s"},
        )


def _issues(m: WaterNetworkModel, closed: set[str]) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    ids = [*(j.id for j in m.junctions), *(r.id for r in m.reservoirs), *(t.id for t in m.tanks)]
    if len(ids) != len(set(ids)):
        out.append(issue("error", "duplicate_node", "Node ids must be unique"))
    g = Graph(directed=False)
    for n in ids:
        g.add_node(n)
    for p in m.pipes:
        if p.node1 not in ids or p.node2 not in ids:
            out.append(issue("error", "invalid_connection", f"Pipe {p.id} connects unknown node(s)"))
        elif p.id not in closed and p.status != "CLOSED":
            g.add_edge(p.node1, p.node2)
    for pu in m.pumps:
        g.add_edge(pu.node1, pu.node2)
    sources = [r.id for r in m.reservoirs] + [t.id for t in m.tanks]
    for j in m.junctions:
        if not g.reaches_any(j.id, sources):
            sev = "error" if j.base_demand_lps > 0 else "warning"
            out.append(
                issue(
                    sev,
                    "isolated_demand" if j.base_demand_lps > 0 else "isolated_node",
                    f"Junction {j.id} is not connected to any reservoir or tank",
                )
            )
    pats = {p.id for p in m.patterns}
    for j in m.junctions:
        if j.pattern_id and j.pattern_id not in pats:
            out.append(
                issue("error", "unknown_pattern", f"Junction {j.id} uses unknown pattern {j.pattern_id}")
            )
    return out
