"""Eclipse SUMO adapter: road traffic scenarios with explicit demand accounting."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.road import RoadNetworkModel, TrafficControls
from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck, ResourceEstimate
from parallel_contracts.metrics import MetricStatus, MetricValue
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
from ..capabilities import SUMO
from ..runtime import ProcessLimits, pinned_requirements, probe_python_runtime, run_python_runner


def _xml(root: ET.Element, path: Path) -> Path:
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _fmt(v: float) -> str:
    return f"{v:.6g}"


class SumoAdapter(EngineAdapter):
    capability = SUMO

    def check_installation(self, mode: ExecutionMode | None = None) -> InstallationCheck:
        mode = mode or ExecutionMode.managed
        code = (
            "import sumo, subprocess, os; b=os.path.join(sumo.SUMO_HOME,'bin');"
            "v=subprocess.run([os.path.join(b,'sumo'),'--version'],capture_output=True,text=True).stdout.splitlines()[0];"
            "n=subprocess.run([os.path.join(b,'netconvert'),'--version'],capture_output=True,text=True).returncode;"
            "import traci; print(v.split()[-1] if v else '', n)"
        )
        ok, out = probe_python_runtime("sumo", code)
        if not ok:
            return self._check(
                mode,
                Availability.installation_required,
                details=[out],
                remedy="Run scripts/setup_runtimes.sh sumo on the simulation worker.",
            )
        version, nc = out.split()
        expected = pinned_requirements("sumo").get("eclipse-sumo", self.capability.pinned_version)
        if nc != "0":
            return self._check(
                mode, Availability.installation_required, version=version, details=["netconvert not runnable"]
            )
        if version != expected:
            return self._check(
                mode, Availability.unsupported_version, version=version, details=[f"pinned {expected}"]
            )
        return self._check(
            mode, Availability.ready, version=version, details=["isolated runtime .runtimes/sumo (TraCI)"]
        )

    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport:
        m = model.content
        if not isinstance(m, RoadNetworkModel):
            return ValidationReport.from_issues(
                [issue("error", "wrong_model", "SUMO requires a road_network model")]
            )
        issues = _connectivity_issues(m, closed=set())
        for f in m.demand:
            if f.provenance.kind.value == "assumption":
                issues.append(
                    issue(
                        "info",
                        "demand_assumption",
                        f"Flow {f.id} demand is an uncalibrated assumption (map geometry does not provide demand).",
                        path=f"content.demand[{f.id}]",
                    )
                )
                break
        for p in m.tls_programs:
            node = next((n for n in m.nodes if n.id == p.node_id), None)
            if node is None or node.type != "traffic_light":
                issues.append(
                    issue("error", "tls_node", f"Signal program for {p.node_id} needs a traffic_light node")
                )
        return ValidationReport.from_issues(issues)

    def _validate_controls(
        self, model: DomainModelEnvelope, scenario: ScenarioContent
    ) -> list[ValidationIssue]:
        m, c = model.content, scenario.controls
        if not isinstance(m, RoadNetworkModel) or not isinstance(c, TrafficControls):
            return [issue("error", "wrong_controls", "Traffic scenario controls required")]
        edge_ids, node_ids = {e.id for e in m.edges}, {n.id for n in m.nodes}
        out = []
        for e in [*c.closed_edges, *c.lane_changes, *c.speed_changes_mps]:
            if e not in edge_ids:
                out.append(issue("error", "unknown_edge", f"Scenario references unknown edge {e}"))
        for n in c.node_type_changes:
            if n not in node_ids:
                out.append(issue("error", "unknown_node", f"Scenario references unknown node {n}"))
        if scenario.settings.seed is None:
            out.append(issue("error", "seed_required", "SUMO runs require an explicit seed"))
        if c.warmup_s >= c.end_s:
            out.append(issue("error", "warmup", "Warm-up must be shorter than the horizon"))
        if c.closed_edges:
            out += _connectivity_issues(m, set(c.closed_edges))
        return out

    def estimate_resources(self, model: DomainModelEnvelope, scenario: ScenarioContent) -> ResourceEstimate:
        m, c = model.content, scenario.controls
        assert isinstance(m, RoadNetworkModel) and isinstance(c, TrafficControls)
        veh = sum(f.number for f in m.demand) * c.demand_scale
        est = 3.0 + veh * c.end_s / m.step_length_s * 2e-6
        return ResourceEstimate(
            cpu_cores=1,
            memory_mb=512,
            expected_runtime_s=round(est, 1),
            basis=f"~{veh:.0f} vehicles x {c.end_s / m.step_length_s:.0f} steps x 2 µs (upper bound)",
        )

    # ---------------------------------------------------------------- preparation
    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m, c = ctx.model.content, ctx.scenario.controls
        assert isinstance(m, RoadNetworkModel) and isinstance(c, TrafficControls)
        d = ctx.workspace.input_dir
        notes: list[str] = []
        warnings: list[str] = []
        closed = set(c.closed_edges)
        for e in sorted(closed):
            notes.append(f"Edge {e} closed: removed from the network.")
        nodes = ET.Element("nodes")
        for n in m.nodes:
            t = c.node_type_changes.get(n.id, n.type)
            if t != n.type:
                notes.append(f"Node {n.id} control changed from {n.type} to {t}.")
            ET.SubElement(nodes, "node", id=n.id, x=_fmt(n.x), y=_fmt(n.y), type=t)
        edges = ET.Element("edges")
        for e in m.edges:
            if e.id in closed:
                continue
            lanes = c.lane_changes.get(e.id, e.num_lanes)
            speed = c.speed_changes_mps.get(e.id, e.speed_mps)
            if lanes != e.num_lanes:
                notes.append(f"Edge {e.id} lanes changed from {e.num_lanes} to {lanes}.")
            if speed != e.speed_mps:
                notes.append(f"Edge {e.id} speed changed from {e.speed_mps:.2f} to {speed:.2f} m/s.")
            attrs = {
                "id": e.id,
                "from": e.from_node,
                "to": e.to_node,
                "numLanes": str(lanes),
                "speed": _fmt(speed),
                "priority": str(e.priority),
            }
            if e.name:
                attrs["name"] = e.name
            if e.shape:
                attrs["shape"] = " ".join(f"{_fmt(p[0])},{_fmt(p[1])}" for p in e.shape)
            ET.SubElement(edges, "edge", attrs)
        files = {"net.nod.xml": _xml(nodes, d / "net.nod.xml"), "net.edg.xml": _xml(edges, d / "net.edg.xml")}
        programs = {p.node_id: p for p in m.tls_programs}
        for p in c.tls_programs:
            programs[p.node_id] = p
            notes.append(f"Signal program for node {p.node_id} replaced ({len(p.phases)} phases).")
        if programs:
            tll = ET.Element("tlLogics")
            for p in programs.values():
                el = ET.SubElement(
                    tll,
                    "tlLogic",
                    id=p.node_id,
                    programID=p.program_id,
                    type="static",
                    offset=_fmt(p.offset_s),
                )
                for ph in p.phases:
                    ET.SubElement(el, "phase", duration=_fmt(ph.duration_s), state=ph.state)
            files["net.tll.xml"] = _xml(tll, d / "net.tll.xml")
        routes = ET.Element("routes")
        for v in m.vtypes:
            ET.SubElement(
                routes,
                "vType",
                id=v.id,
                accel=_fmt(v.accel),
                decel=_fmt(v.decel),
                sigma=_fmt(v.sigma),
                length=_fmt(v.length),
                minGap=_fmt(v.min_gap),
                maxSpeed=_fmt(v.max_speed),
                vClass=v.vclass,
            )
        scheduled = 0
        excluded = []
        if c.demand_scale != 1.0:
            notes.append(f"Demand scaled by {c.demand_scale:g} (flow counts rounded to whole vehicles).")
        flows_out = []
        for f in sorted(m.demand, key=lambda f: (f.begin_s, f.id)):
            n = int(round(f.number * c.demand_scale))
            scheduled += n
            if f.from_edge in closed or f.to_edge in closed:
                excluded.append({"flow": f.id, "vehicles": n, "reason": "origin or destination edge closed"})
                continue
            if n == 0:
                continue
            flows_out.append(f)
            ET.SubElement(
                routes,
                "flow",
                id=f.id,
                begin=_fmt(f.begin_s),
                end=_fmt(f.end_s),
                number=str(n),
                **{
                    "from": f.from_edge,
                    "to": f.to_edge,
                    "type": f.vtype,
                    "departLane": "best",
                    "departSpeed": "max",
                },
            )
        if excluded:
            warnings.append(
                f"{sum(x['vehicles'] for x in excluded)} scheduled vehicles cannot depart or arrive because "
                "their origin/destination edge is closed; they are reported as unfinished trips."
            )
        files["routes.rou.xml"] = _xml(routes, d / "routes.rou.xml")
        last_depart = max((f.end_s for f in flows_out), default=0.0)
        runner = {
            "end_s": c.end_s,
            "step_length_s": m.step_length_s,
            "seed": ctx.seed,
            "queue_period_s": 5.0,
            "fcd_period_s": c.fcd_period_s,
            "last_depart_s": last_depart,
        }
        files["runner_input.json"] = write_json(d / "runner_input.json", runner)
        accounting = {
            "scheduled": scheduled,
            "closure_excluded": excluded,
            "sumo_scheduled": scheduled - sum(x["vehicles"] for x in excluded),
            "warmup_s": c.warmup_s,
            "end_s": c.end_s,
        }
        files["accounting.json"] = write_json(d / "accounting.json", accounting)
        return PreparedInputs(
            files=files,
            hashes=hash_inputs(files),
            model_change_notes=notes,
            warnings=warnings,
            summary={
                "scheduled_trips": scheduled,
                "edges": len(m.edges) - len(closed),
                "demand_hash": hash_inputs({"r": files["routes.rou.xml"]})["r"],
            },
        )

    def execute(self, ctx: RunContext, prepared: PreparedInputs, monitor: RunMonitor) -> ExecutionResult:
        started = now()
        log = ctx.workspace.log_dir / "sumo.log"
        out = run_python_runner(
            "sumo",
            "sumo_runner.py",
            cwd=ctx.workspace.root,
            args=[str(ctx.workspace.input_dir), str(ctx.workspace.output_dir)],
            log_path=log,
            monitor=monitor,
            limits=ProcessLimits(timeout_s=float(ctx.scenario.settings.timeout_s)),
            progress_base=0.05,
            progress_span=0.85,
        )
        rr = json.loads((ctx.workspace.output_dir / "runner_result.json").read_text())
        err = ctx.workspace.output_dir / "sumo-errors.log"
        warns = list(out.warnings)
        if err.exists() and err.stat().st_size:
            lines = [ln for ln in err.read_text(errors="replace").splitlines() if ln.strip()]
            warns.append(
                f"SUMO reported {len(lines)} warning/error lines (see sumo-errors.log); first: {lines[0][:200]}"
            )
        return ExecutionResult(
            engine_version=rr["engine_version"],
            runtime=rr,
            exit_code=out.exit_code,
            stdout_log=log,
            warnings=warns,
            started_at=started,
            finished_at=now(),
        )

    # ---------------------------------------------------------------- normalisation
    def normalise_results(
        self, ctx: RunContext, prepared: PreparedInputs, execution: ExecutionResult
    ) -> NormalisedResult:
        o = ctx.workspace.output_dir
        acc = json.loads(prepared.files["accounting.json"].read_text())
        warmup, end = acc["warmup_s"], acc["end_s"]
        trips = [dict(el.attrib) for el in _iter(o / "tripinfo.xml", "tripinfo")]
        completed = [t for t in trips if float(t.get("arrival", "-1")) >= 0]
        unfinished_records = [t for t in trips if float(t.get("arrival", "-1")) < 0]
        stats = ET.parse(o / "statistics.xml").getroot()
        veh = stats.find("vehicles").attrib  # type: ignore[union-attr]
        running = int(veh.get("running", 0))
        teleports = int(stats.find("teleports").attrib.get("total", 0))  # type: ignore[union-attr]
        scheduled = acc["scheduled"]
        closure_excl = sum(x["vehicles"] for x in acc["closure_excluded"])
        n_comp = len(completed)
        not_inserted = acc["sumo_scheduled"] - n_comp - running + closure_excl
        measured = [t for t in completed if float(t["depart"]) >= warmup]
        dur = np.array([float(t["duration"]) for t in measured])
        loss = np.array([float(t["timeLoss"]) for t in measured])
        arr_in = sum(1 for t in completed if warmup <= float(t["arrival"]) <= end)
        metrics = [
            MetricValue(metric_id="traffic.scheduled_trips", value=scheduled, unit="veh"),
            MetricValue(metric_id="traffic.completed_trips", value=n_comp, unit="veh"),
            MetricValue(
                metric_id="traffic.unfinished_trips",
                value=scheduled - n_comp,
                unit="veh",
                note=f"{running} still driving at the end, {not_inserted} never inserted",
            ),
            MetricValue(metric_id="traffic.not_inserted", value=not_inserted, unit="veh"),
            MetricValue(
                metric_id="traffic.completion_ratio",
                value=n_comp / scheduled if scheduled else None,
                unit="1",
                status=MetricStatus.available if scheduled else MetricStatus.unavailable,
            ),
            MetricValue(
                metric_id="traffic.throughput", value=arr_in / ((end - warmup) / 3600.0), unit="veh/h"
            ),
            MetricValue(metric_id="traffic.teleports", value=teleports, unit="count"),
        ]
        if len(dur):
            metrics += [
                MetricValue(
                    metric_id="traffic.mean_travel_time",
                    value=float(dur.mean()),
                    unit="s",
                    note=f"over {len(dur)} completed trips; completion ratio must be read alongside",
                ),
                MetricValue(
                    metric_id="traffic.p95_travel_time", value=float(np.percentile(dur, 95)), unit="s"
                ),
                MetricValue(metric_id="traffic.mean_time_loss", value=float(loss.mean()), unit="s"),
            ]
        else:
            for mid in ("traffic.mean_travel_time", "traffic.p95_travel_time", "traffic.mean_time_loss"):
                metrics.append(
                    MetricValue(
                        metric_id=mid,
                        value=None,
                        unit="s",
                        status=MetricStatus.unavailable,
                        note="No trips completed after the warm-up period.",
                    )
                )
        # queues
        qmax: dict[str, float] = {}
        for data in _iter(o / "queue.xml", "lane"):
            lane = data.attrib["id"]
            edge = lane.rsplit("_", 1)[0]
            if edge.startswith(":"):
                continue  # internal junction lanes
            qmax[edge] = max(qmax.get(edge, 0.0), float(data.attrib.get("queueing_length", 0.0)))
        for e in ctx.model.content.edges:  # type: ignore[union-attr]
            if e.id in qmax or e.id not in ctx.scenario.controls.closed_edges:  # type: ignore[union-attr]
                metrics.append(
                    MetricValue(
                        metric_id="traffic.max_queue_length", value=qmax.get(e.id, 0.0), unit="m", entity=e.id
                    )
                )
        metrics.append(
            MetricValue(
                metric_id="traffic.network_max_queue_length", value=max(qmax.values(), default=0.0), unit="m"
            )
        )
        crosschecks = [
            crosscheck(
                "unfinished tripinfo records vs statistics 'running'",
                float(len(unfinished_records)),
                float(running),
                0,
                0,
                "write-unfinished emits one record per vehicle still running at the end.",
            ),
        ]
        vts = stats.find("vehicleTripStatistics")
        if vts is not None and completed:
            native_mean = float(vts.attrib.get("duration", "nan"))
            all_dur = float(np.mean([float(t["duration"]) for t in completed]))
            crosschecks.append(
                crosscheck(
                    "mean duration of all completed trips vs SUMO vehicleTripStatistics",
                    all_dur,
                    native_mean,
                    0.01,
                    1e-4,
                    "SUMO reports the same mean rounded to 2 decimals.",
                )
            )
        crosschecks.append(
            crosscheck(
                "demand accounting: completed + running + not inserted = scheduled",
                float(n_comp + running + not_inserted),
                float(scheduled),
                0,
                0,
                "Every scheduled vehicle must be accounted for.",
            )
        )
        warnings = list(prepared.warnings) + list(execution.warnings)
        if teleports:
            warnings.append(f"{teleports} teleports occurred; delay figures are affected.")
        if not_inserted:
            warnings.append(
                f"{not_inserted} scheduled vehicles were never inserted; compare completion ratios before "
                "comparing travel times."
            )
        files = self._fcd(o, ctx.workspace.normalised_dir)
        summary = {
            "scheduled": scheduled,
            "completed": n_comp,
            "running_at_end": running,
            "not_inserted": not_inserted,
            "warmup_s": warmup,
            "end_s": end,
            "demand_hash": prepared.summary["demand_hash"],
            "delay_definition": "SUMO timeLoss: time lost due to driving below the ideal speed (incl. waiting).",
        }
        trips_tbl = pa.table(
            {
                "vehicle": [t["id"] for t in trips],
                "depart_s": [float(t["depart"]) for t in trips],
                "arrival_s": [float(t.get("arrival", -1)) for t in trips],
                "duration_s": [float(t.get("duration", -1)) for t in trips],
                "time_loss_s": [float(t.get("timeLoss", 0)) for t in trips],
                "route_length_m": [float(t.get("routeLength", 0)) for t in trips],
            }
        )
        pq.write_table(trips_tbl, ctx.workspace.normalised_dir / "trips.parquet")
        files.append(
            NormalisedFile(
                "trips.parquet",
                ctx.workspace.normalised_dir / "trips.parquet",
                "normalised",
                "application/vnd.apache.parquet",
                "Per-vehicle trip records (incl. unfinished)",
            )
        )
        return NormalisedResult(
            metrics=metrics,
            files=files,
            warnings=warnings,
            crosschecks=crosschecks,
            summary=summary,
            units={"time": "s", "length": "m"},
        )

    def _fcd(self, o: Path, norm: Path) -> list[NormalisedFile]:
        ts, vid, xs, ys, ang, spd = [], [], [], [], [], []
        for step in _iter(o / "fcd.xml", "timestep"):
            t = float(step.attrib["time"])
            for v in step.findall("vehicle"):
                ts.append(t)
                vid.append(v.attrib["id"])
                xs.append(float(v.attrib["x"]))
                ys.append(float(v.attrib["y"]))
                ang.append(float(v.attrib.get("angle", 0)))
                spd.append(float(v.attrib.get("speed", 0)))
        tbl = pa.table({"t_s": ts, "vehicle": vid, "x": xs, "y": ys, "angle_deg": ang, "speed_mps": spd})
        pq.write_table(tbl, norm / "vehicles.parquet", compression="zstd")
        names = sorted(set(vid))
        idx = {n: i for i, n in enumerate(names)}
        frames: dict[float, list] = {}
        for t, v, x, y, a in zip(ts, vid, xs, ys, ang, strict=True):
            fr = frames.setdefault(t, [[], [], []])
            fr[0].append(idx[v])
            fr[1].extend([round(x, 2), round(y, 2)])
            fr[2].append(round(a, 1))
        playback = {
            "agents": names,
            "kind": "vehicles",
            "frames": [{"t": t, "i": f[0], "xy": f[1], "angle": f[2]} for t, f in sorted(frames.items())],
            "note": "Derived for display from fcd.xml; metrics use tripinfo/statistics outputs.",
        }
        (norm / "playback.json").write_text(json.dumps(playback, separators=(",", ":")))
        return [
            NormalisedFile(
                "vehicles.parquet",
                norm / "vehicles.parquet",
                "trajectory",
                "application/vnd.apache.parquet",
                "Vehicle positions (FCD), local metres",
            ),
            NormalisedFile(
                "playback.json",
                norm / "playback.json",
                "trajectory",
                "application/json",
                "Browser playback derivative of FCD output",
            ),
        ]


def _iter(path: Path, tag: str):
    if not path.exists():
        return
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag == tag:
            yield el
            if tag != "lane":
                el.clear()


def _connectivity_issues(m: RoadNetworkModel, closed: set[str]) -> list[ValidationIssue]:
    g = Graph(directed=True)
    edge_by_id = {e.id: e for e in m.edges}
    for n in m.nodes:
        g.add_node(n.id)
    for e in m.edges:
        if e.id not in closed:
            g.add_edge(e.from_node, e.to_node)
    out = []
    for f in m.demand:
        if f.from_edge in closed or f.to_edge in closed:
            out.append(
                issue(
                    "warning",
                    "demand_on_closed_edge",
                    f"Flow {f.id} starts or ends on a closed edge; its vehicles will be reported as unfinished.",
                )
            )
            continue
        a, b = edge_by_id[f.from_edge], edge_by_id[f.to_edge]
        if (
            not (a.to_node == b.from_node or b.from_node in g.reachable_from(a.to_node))
            and f.from_edge != f.to_edge
        ):
            sev = "warning" if closed else "error"
            out.append(
                issue(
                    sev,
                    "missing_connectivity",
                    f"Flow {f.id}: no directed path from edge {f.from_edge} to edge {f.to_edge}"
                    + (" after closures; its vehicles will be reported as unfinished." if closed else "."),
                    hint="Check edge directions or add the missing link.",
                )
            )
    return out
