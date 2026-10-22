"""EPA SWMM adapter: rainfall-runoff and drainage network hydraulics."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.drainage import (
    Conduit,
    DrainageControls,
    DrainageNetworkModel,
    StorageUnit,
)
from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck, ResourceEstimate
from parallel_contracts.metrics import MetricStatus, MetricValue
from parallel_contracts.runs import ValidationIssue, ValidationReport
from parallel_contracts.scenario import ScenarioContent
from parallel_metrics.series import duration_above, trapezoid_volume
from parallel_scene.networks import Graph

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
from ..capabilities import SWMM
from ..runtime import ProcessLimits, pinned_requirements, probe_python_runtime, run_python_runner

HA_M_TO_M3 = 10_000.0
ML_TO_M3 = 1_000.0  # 10^6 litres


def filled_circular_area_fraction(depth_fraction: float) -> float:
    """Fraction of a circular cross-section occupied by sediment of the given depth fraction."""
    theta = 2.0 * math.acos(1.0 - 2.0 * depth_fraction)
    return (theta - math.sin(theta)) / (2.0 * math.pi)


def apply_controls(m: DrainageNetworkModel, c: DrainageControls) -> tuple[DrainageNetworkModel, list[str]]:
    """Return the scenario's model plus a human-readable account of every change made."""
    m = m.model_copy(deep=True)
    notes: list[str] = []
    if c.rainfall_series_id:
        for g in m.raingage_series:
            m.raingage_series[g] = c.rainfall_series_id
        notes.append(f"All rain gages use rainfall series '{c.rainfall_series_id}'.")
    if c.rainfall_scale != 1.0:
        for r in m.rainfall:
            r.intensities_mm_per_h = [v * c.rainfall_scale for v in r.intensities_mm_per_h]
        notes.append(f"Rainfall intensities scaled by {c.rainfall_scale:g}.")
    conduits = {x.id: x for x in m.conduits}
    for b in c.blockages:
        x = conduits[b.conduit_id]
        if x.shape not in ("CIRCULAR", "FILLED_CIRCULAR"):
            raise EngineError(
                f"blockage of {x.id}: only circular conduits support the FILLED_CIRCULAR representation",
                deterministic=True,
            )
        x.shape = "FILLED_CIRCULAR"
        x.geom2_m = b.sediment_depth_fraction * x.geom1_m
        notes.append(
            f"Conduit {x.id} blockage: FILLED_CIRCULAR with sediment depth {x.geom2_m:.3f} m "
            f"({b.sediment_depth_fraction:.0%} of {x.geom1_m:.3f} m diameter; "
            f"{filled_circular_area_fraction(b.sediment_depth_fraction):.1%} of the cross-section area)."
        )
    for r in c.resizes:
        x = conduits[r.conduit_id]
        notes.append(f"Conduit {x.id} diameter changed from {x.geom1_m:.3f} m to {r.diameter_m:.3f} m.")
        x.geom1_m = r.diameter_m
    for s in c.storage_conversions:
        j = next(j for j in m.junctions if j.id == s.junction_id)
        depth = s.max_depth_m or j.max_depth_m
        m.junctions = [x for x in m.junctions if x.id != j.id]
        m.storage.append(
            StorageUnit(
                id=j.id,
                position=j.position,
                invert_elev_m=j.invert_elev_m,
                max_depth_m=depth,
                area_m2=s.area_m2,
            )
        )
        notes.append(
            f"Junction {j.id} replaced by a storage unit at the same invert ({s.area_m2:.0f} m² plan area, "
            f"{depth:.2f} m deep, {s.area_m2 * depth:.0f} m³ capacity)."
        )
    for lc in c.lid_controls:
        m.lid_controls.append(lc)
    for u in c.lid_usage:
        m.lid_usage.append(u)
        notes.append(
            f"Subcatchment {u.subcatchment_id}: {u.number} x {u.area_each_m2:.0f} m² bio-retention cells "
            f"({u.lid_id}) treating {u.from_imperv_pct:.0f}% of impervious runoff."
        )
    for p in c.pump_changes:
        pump = next(x for x in m.pumps if x.id == p.pump_id)
        pump.startup_depth_m, pump.shutoff_depth_m = p.startup_depth_m, p.shutoff_depth_m
        if p.initially_on is not None:
            pump.initially_on = p.initially_on
        notes.append(
            f"Pump {pump.id} control: on at {p.startup_depth_m:.2f} m, off at {p.shutoff_depth_m:.2f} m."
        )
    for cc in c.catchment_changes:
        sc = next(x for x in m.subcatchments if x.id == cc.subcatchment_id)
        notes.append(
            f"Subcatchment {sc.id} imperviousness changed from {sc.imperv_pct:.0f}% to {cc.imperv_pct:.0f}%."
        )
        sc.imperv_pct = cc.imperv_pct
    return DrainageNetworkModel.model_validate(m.model_dump()), notes


def _hm(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def write_inp(m: DrainageNetworkModel, path: Path) -> Path:
    sd, st = m.start_datetime.split()
    ed, et = m.end_datetime.split()
    L: list[str] = ["[TITLE]", "Generated by Parallel (drainage_network schema 1.0)", ""]
    opts = {
        "FLOW_UNITS": "CMS",
        "INFILTRATION": "HORTON",
        "FLOW_ROUTING": "DYNWAVE",
        "LINK_OFFSETS": "DEPTH",
        "MIN_SLOPE": "0",
        "ALLOW_PONDING": "NO",
        "SKIP_STEADY_STATE": "NO",
        "START_DATE": sd,
        "START_TIME": st,
        "REPORT_START_DATE": sd,
        "REPORT_START_TIME": st,
        "END_DATE": ed,
        "END_TIME": et,
        "DRY_DAYS": "0",
        "REPORT_STEP": _hm(m.report_step_s),
        "WET_STEP": _hm(m.wet_step_s),
        "DRY_STEP": _hm(m.dry_step_s),
        "ROUTING_STEP": f"{m.routing_step_s:g}",
        "INERTIAL_DAMPING": "PARTIAL",
        "NORMAL_FLOW_LIMITED": "BOTH",
        "FORCE_MAIN_EQUATION": "H-W",
        "VARIABLE_STEP": "0.75",
        "LENGTHENING_STEP": "0",
        "MIN_SURFAREA": "1.167",
        "MAX_TRIALS": "8",
        "HEAD_TOLERANCE": "0.0015",
        "SYS_FLOW_TOL": "5",
        "LAT_FLOW_TOL": "5",
        "MINIMUM_STEP": "0.5",
        "THREADS": "1",
    }
    L += ["[OPTIONS]"] + [f"{k:<22}{v}" for k, v in opts.items()] + [""]
    L += ["[RAINGAGES]"]
    series = {r.id: r for r in m.rainfall}
    for g, sid in m.raingage_series.items():
        L.append(f"{g:<16} INTENSITY {_hm(series[sid].interval_min * 60)[:-3]} 1.0 TIMESERIES {sid}")
    L.append("")
    if m.subcatchments:
        L += ["[SUBCATCHMENTS]"]
        for s in m.subcatchments:
            L.append(
                f"{s.id:<16} {s.raingage:<16} {s.outlet:<16} {s.area_ha:g} {s.imperv_pct:g} {s.width_m:g} {s.slope_pct:g} 0"
            )
        L += ["", "[SUBAREAS]"]
        for s in m.subcatchments:
            L.append(
                f"{s.id:<16} {s.n_imperv:g} {s.n_perv:g} {s.dstore_imperv_mm:g} {s.dstore_perv_mm:g} {s.pct_zero_imperv:g} OUTLET"
            )
        L += ["", "[INFILTRATION]"]
        for s in m.subcatchments:
            h = s.infiltration
            L.append(
                f"{s.id:<16} {h.max_rate_mm_per_h:g} {h.min_rate_mm_per_h:g} {h.decay_per_h:g} {h.dry_time_d:g} {h.max_infil_mm:g}"
            )
        L.append("")
    if m.lid_controls:
        L += ["[LID_CONTROLS]"]
        for lc in m.lid_controls:
            L.append(f"{lc.id:<16} BC")
            L.append(
                f"{lc.id:<16} SURFACE {lc.surface_storage_mm:g} {lc.surface_veg_fraction:g} {lc.surface_roughness:g} {lc.surface_slope_pct:g} 5"
            )
            L.append(
                f"{lc.id:<16} SOIL {lc.soil_thickness_mm:g} {lc.soil_porosity:g} {lc.soil_field_capacity:g} "
                f"{lc.soil_wilting_point:g} {lc.soil_ksat_mm_per_h:g} {lc.soil_conductivity_slope:g} {lc.soil_suction_head_mm:g}"
            )
            L.append(
                f"{lc.id:<16} STORAGE {lc.storage_thickness_mm:g} {lc.storage_void_ratio:g} {lc.storage_seepage_mm_per_h:g} {lc.storage_clogging:g}"
            )
            L.append(f"{lc.id:<16} DRAIN {lc.drain_coeff:g} {lc.drain_exponent:g} {lc.drain_offset_mm:g} 6")
        L.append("")
    if m.lid_usage:
        L += ["[LID_USAGE]"]
        for u in m.lid_usage:
            L.append(
                f"{u.subcatchment_id:<16} {u.lid_id:<16} {u.number} {u.area_each_m2:g} {u.width_m:g} "
                f"{u.init_saturation_pct:g} {u.from_imperv_pct:g} {1 if u.to_pervious else 0}"
            )
        L.append("")
    if m.junctions:
        L += ["[JUNCTIONS]"]
        for j in m.junctions:
            L.append(
                f"{j.id:<16} {j.invert_elev_m:g} {j.max_depth_m:g} {j.init_depth_m:g} {j.surcharge_depth_m:g} {j.ponded_area_m2:g}"
            )
        L.append("")
    L += ["[OUTFALLS]"]
    for o in m.outfalls:
        stage = f"{o.fixed_stage_m:g}" if o.type == "FIXED" else ""
        L.append(f"{o.id:<16} {o.invert_elev_m:g} {o.type} {stage} NO")
    L.append("")
    if m.storage:
        L += ["[STORAGE]"]
        for s in m.storage:
            L.append(
                f"{s.id:<16} {s.invert_elev_m:g} {s.max_depth_m:g} {s.init_depth_m:g} FUNCTIONAL 0 0 {s.area_m2:g} 0 0"
            )
        L.append("")
    L += ["[CONDUITS]"]
    for x in m.conduits:
        L.append(
            f"{x.id:<16} {x.from_node:<16} {x.to_node:<16} {x.length_m:g} {x.roughness_n:g} {x.in_offset_m:g} {x.out_offset_m:g} 0 0"
        )
    if m.pumps:
        L += ["", "[PUMPS]"]
        for p in m.pumps:
            L.append(
                f"{p.id:<16} {p.from_node:<16} {p.to_node:<16} {p.curve_id:<16} {'ON' if p.initially_on else 'OFF'} "
                f"{p.startup_depth_m:g} {p.shutoff_depth_m:g}"
            )
    L += ["", "[XSECTIONS]"]
    for x in m.conduits:
        L.append(f"{x.id:<16} {x.shape:<16} {x.geom1_m:g} {x.geom2_m:g} {x.geom3:g} {x.geom4:g} {x.barrels}")
    L.append("")
    if m.pump_curves:
        L += ["[CURVES]"]
        for c in m.pump_curves:
            for i, (xv, yv) in enumerate(c.points):
                L.append(f"{c.id:<16} {(c.type.capitalize() if i == 0 else ''):<10} {xv:g} {yv:g}")
        L.append("")
    L += ["[TIMESERIES]"]
    for r in m.rainfall:
        for i, v in enumerate(r.intensities_mm_per_h):
            L.append(f"{r.id:<16} {_hm(i * r.interval_min * 60)[:-3]} {v:g}")
        L.append(f"{r.id:<16} {_hm(len(r.intensities_mm_per_h) * r.interval_min * 60)[:-3]} 0")
    L += ["", "[REPORT]", "INPUT NO", "CONTROLS NO", "SUBCATCHMENTS ALL", "NODES ALL", "LINKS ALL", ""]
    L += ["[COORDINATES]"]
    for n in [*m.junctions, *m.outfalls, *m.storage]:
        L.append(f"{n.id:<16} {n.position[0]:g} {n.position[1]:g}")
    verts = [(x.id, v) for x in m.conduits for v in x.vertices]
    if verts:
        L += ["", "[VERTICES]"] + [f"{lid:<16} {v[0]:g} {v[1]:g}" for lid, v in verts]
    polys = [(s.id, p) for s in m.subcatchments if s.polygon for p in s.polygon.coordinates[0]]
    if polys:
        L += ["", "[Polygons]"] + [f"{sid:<16} {p[0]:g} {p[1]:g}" for sid, p in polys]
    path.write_text("\n".join(L) + "\n")
    return path


class SwmmAdapter(EngineAdapter):
    capability = SWMM

    def check_installation(self, mode: ExecutionMode | None = None) -> InstallationCheck:
        mode = mode or ExecutionMode.managed
        ok, out = probe_python_runtime(
            "swmm",
            "import swmm.toolkit as t, pyswmm; from swmm.toolkit import solver;"
            "print(t.__version__, pyswmm.__version__, solver.swmm_get_version())",
        )
        if not ok:
            return self._check(
                mode,
                Availability.installation_required,
                details=[out],
                remedy="Run scripts/setup_runtimes.sh swmm on the simulation worker.",
            )
        tk, ps, eng = out.split()
        pins = pinned_requirements("swmm")
        if tk != pins.get("swmm-toolkit") or ps != pins.get("pyswmm"):
            return self._check(
                mode,
                Availability.unsupported_version,
                version=f"swmm-toolkit {tk}",
                details=[f"pinned swmm-toolkit {pins.get('swmm-toolkit')}, pyswmm {pins.get('pyswmm')}"],
            )
        e = str(eng)
        engine = f"{e[0]}.{e[1]}.{int(e[2:])}" if len(e) >= 4 else e
        return self._check(
            mode, Availability.ready, version=f"SWMM {engine} (swmm-toolkit {tk}, pyswmm {ps})"
        )

    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport:
        m = model.content
        if not isinstance(m, DrainageNetworkModel):
            return ValidationReport.from_issues(
                [issue("error", "wrong_model", "SWMM requires a drainage_network model")]
            )
        return ValidationReport.from_issues(_network_issues(m))

    def _validate_controls(
        self, model: DomainModelEnvelope, scenario: ScenarioContent
    ) -> list[ValidationIssue]:
        m, c = model.content, scenario.controls
        if not isinstance(m, DrainageNetworkModel) or not isinstance(c, DrainageControls):
            return [issue("error", "wrong_controls", "Drainage scenario controls required")]
        out = []
        cids = {x.id for x in m.conduits}
        for b in c.blockages:
            if b.conduit_id not in cids:
                out.append(
                    issue("error", "unknown_conduit", f"Blockage references unknown conduit {b.conduit_id}")
                )
        for r in c.resizes:
            if r.conduit_id not in cids:
                out.append(
                    issue("error", "unknown_conduit", f"Resize references unknown conduit {r.conduit_id}")
                )
        jids = {j.id for j in m.junctions}
        for s in c.storage_conversions:
            if s.junction_id not in jids:
                out.append(
                    issue(
                        "error",
                        "unknown_junction",
                        f"Storage conversion references unknown junction {s.junction_id}",
                    )
                )
        if c.rainfall_series_id and c.rainfall_series_id not in {r.id for r in m.rainfall}:
            out.append(issue("error", "unknown_rainfall", f"Unknown rainfall series {c.rainfall_series_id}"))
        scids = {s.id for s in m.subcatchments}
        lids = {lc.id for lc in [*m.lid_controls, *c.lid_controls]}
        for u in c.lid_usage:
            if u.subcatchment_id not in scids or u.lid_id not in lids:
                out.append(
                    issue(
                        "error",
                        "lid_ref",
                        f"LID usage references unknown subcatchment or LID ({u.subcatchment_id}/{u.lid_id})",
                    )
                )
            else:
                sc = next(s for s in m.subcatchments if s.id == u.subcatchment_id)
                if u.number * u.area_each_m2 >= sc.area_ha * 10_000:
                    out.append(issue("error", "lid_area", f"LID area exceeds subcatchment {sc.id} area"))
        if not out:
            try:
                applied, _ = apply_controls(m, c)
                out += [i for i in _network_issues(applied) if i.severity == "error"]
            except (EngineError, StopIteration, ValueError) as exc:
                out.append(issue("error", "scenario_application", str(exc)))
        return out

    def estimate_resources(self, model: DomainModelEnvelope, scenario: ScenarioContent) -> ResourceEstimate:
        m = model.content
        assert isinstance(m, DrainageNetworkModel)
        n = len(m.conduits) + len(m.junctions)
        return ResourceEstimate(
            cpu_cores=1,
            memory_mb=256,
            expected_runtime_s=round(2 + n * 0.01, 1),
            basis=f"{n} network elements; small networks run in seconds",
        )

    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m, c = ctx.model.content, ctx.scenario.controls
        assert isinstance(m, DrainageNetworkModel) and isinstance(c, DrainageControls)
        applied, notes = apply_controls(m, c)
        d = ctx.workspace.input_dir
        files = {
            "model.inp": write_inp(applied, d / "model.inp"),
            "applied_model.json": write_json(d / "applied_model.json", applied.model_dump(mode="json")),
        }
        series = {r.id: r for r in applied.rainfall}
        used = sorted(set(applied.raingage_series.values()))
        rain_hash = hash_inputs(
            {"r": write_json(d / "rainfall.json", [series[s].model_dump(mode="json") for s in used])}
        )["r"]
        files["rainfall.json"] = d / "rainfall.json"
        return PreparedInputs(
            files=files,
            hashes=hash_inputs(files),
            model_change_notes=notes,
            summary={
                "rainfall_series": used,
                "rainfall_hash": rain_hash,
                "rainfall_depth_mm": {s: round(series[s].total_depth_mm, 2) for s in used},
                "vertical_reference": applied.vertical_reference,
                "initial_conditions": {j.id: j.init_depth_m for j in applied.junctions},
            },
        )

    def execute(self, ctx: RunContext, prepared: PreparedInputs, monitor: RunMonitor) -> ExecutionResult:
        started = now()
        log = ctx.workspace.log_dir / "swmm.log"
        out = run_python_runner(
            "swmm",
            "swmm_runner.py",
            cwd=ctx.workspace.root,
            args=[str(ctx.workspace.input_dir), str(ctx.workspace.output_dir)],
            log_path=log,
            monitor=monitor,
            limits=ProcessLimits(timeout_s=float(ctx.scenario.settings.timeout_s)),
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

    def normalise_results(
        self, ctx: RunContext, prepared: PreparedInputs, execution: ExecutionResult
    ) -> NormalisedResult:
        o = ctx.workspace.output_dir
        applied = DrainageNetworkModel.model_validate_json(prepared.files["applied_model.json"].read_text())
        rpt = parse_report((o / "model.rpt").read_text(errors="replace"))
        nodes = _read_series(o / "nodes.csv", "node")
        links = _read_series(o / "links.csv", "link")
        system = _read_rows(o / "system.csv")
        t_sys = [r["t_s"] for r in system]
        # SWMM report values are instantaneous at the end of each reporting step; prepend t=0 state of zero flow
        t0 = [0.0] + t_sys
        metrics: list[MetricValue] = []
        flood_total = trapezoid_volume(t0, [0.0] + [r["flooding_cms"] for r in system])
        runoff_total = trapezoid_volume(t0, [0.0] + [r["runoff_cms"] for r in system])
        metrics.append(MetricValue(metric_id="drainage.total_flooding_volume", value=flood_total, unit="m3"))
        if applied.subcatchments:
            metrics.append(
                MetricValue(metric_id="drainage.total_runoff_volume", value=runoff_total, unit="m3")
            )
            if "precipitation_ha_m" in rpt:
                metrics.append(
                    MetricValue(
                        metric_id="drainage.total_rainfall_volume",
                        value=rpt["precipitation_ha_m"] * HA_M_TO_M3,
                        unit="m3",
                    )
                )
            if "runoff_continuity_pct" in rpt:
                metrics.append(
                    MetricValue(
                        metric_id="drainage.runoff_continuity_error",
                        value=rpt["runoff_continuity_pct"],
                        unit="%",
                    )
                )
        metrics.append(
            MetricValue(
                metric_id="drainage.routing_continuity_error",
                value=rpt.get("routing_continuity_pct"),
                unit="%",
                status=MetricStatus.available
                if "routing_continuity_pct" in rpt
                else MetricStatus.unavailable,
            )
        )
        crown = _node_crowns(applied)
        outfalls = {x.id for x in applied.outfalls}
        storages = {x.id for x in applied.storage}
        for nid, ser in sorted(nodes.items()):
            t = [0.0] + [r["t_s"] for r in ser]
            fl = [0.0] + [r["flooding_cms"] for r in ser]
            depth = [r["depth_m"] for r in ser]
            metrics.append(
                MetricValue(metric_id="drainage.max_node_depth", value=max(depth), unit="m", entity=nid)
            )
            if nid in outfalls:
                metrics.append(
                    MetricValue(
                        metric_id="drainage.peak_outfall_flow",
                        value=max(r["total_inflow_cms"] for r in ser),
                        unit="m3/s",
                        entity=nid,
                    )
                )
                continue
            metrics.append(
                MetricValue(
                    metric_id="drainage.node_flooding_volume",
                    value=trapezoid_volume(t, fl),
                    unit="m3",
                    entity=nid,
                )
            )
            metrics.append(
                MetricValue(
                    metric_id="drainage.node_flood_duration",
                    value=duration_above(t, fl, 1e-6) / 3600,
                    unit="h",
                    entity=nid,
                )
            )
            if nid in storages:
                metrics.append(
                    MetricValue(
                        metric_id="drainage.max_storage_depth", value=max(depth), unit="m", entity=nid
                    )
                )
            elif nid in crown:
                metrics.append(
                    MetricValue(
                        metric_id="drainage.node_surcharge_duration",
                        value=duration_above(t, [0.0, *depth], crown[nid] + 1e-6) / 3600,
                        unit="h",
                        entity=nid,
                    )
                )
        for lid, ser in sorted(links.items()):
            metrics.append(
                MetricValue(
                    metric_id="drainage.conduit_max_fill",
                    value=max(r["capacity"] for r in ser),
                    unit="1",
                    entity=lid,
                )
            )
            if lid in rpt.get("link_max_full_flow", {}):
                metrics.append(
                    MetricValue(
                        metric_id="drainage.conduit_max_flow_ratio",
                        value=rpt["link_max_full_flow"][lid],
                        unit="1",
                        entity=lid,
                    )
                )
        crosschecks = []
        if "flooding_loss_ha_m" in rpt:
            crosschecks.append(
                crosscheck(
                    "system flooding volume vs report 'Flooding Loss'",
                    flood_total,
                    rpt["flooding_loss_ha_m"] * HA_M_TO_M3,
                    abs_tol=10.0,
                    rel_tol=0.05,
                    why="Report-step trapezoidal integration vs SWMM's routing-step accumulation; the report rounds to "
                    "0.001 hectare-metre (10 m³).",
                )
            )
        if applied.subcatchments and "surface_runoff_ha_m" in rpt:
            crosschecks.append(
                crosscheck(
                    "runoff volume vs report 'Surface Runoff'",
                    runoff_total,
                    rpt["surface_runoff_ha_m"] * HA_M_TO_M3,
                    abs_tol=10.0,
                    rel_tol=0.05,
                    why="Report-step trapezoidal integration vs SWMM's runoff-step accumulation; 10 m³ report rounding.",
                )
            )
        for nid, vol_ml in rpt.get("node_flood_volume_ml", {}).items():
            mv = next(
                (
                    x.value
                    for x in metrics
                    if x.metric_id == "drainage.node_flooding_volume" and x.entity == nid
                ),
                None,
            )
            crosschecks.append(
                crosscheck(
                    f"node {nid} flood volume vs Node Flooding Summary",
                    mv,
                    vol_ml * ML_TO_M3,
                    abs_tol=2.0,
                    rel_tol=0.05,
                    why="Report-step integration vs routing-step accumulation; 1 m³ report rounding.",
                )
            )
        warnings = list(execution.warnings)
        rc = rpt.get("routing_continuity_pct")
        if rc is not None and abs(rc) > 1.0:
            warnings.append(
                f"Flow routing continuity error is {rc:.2f}% (above 1%): results may be numerically unstable."
            )
        ro = rpt.get("runoff_continuity_pct")
        if ro is not None and abs(ro) > 1.0:
            warnings.append(f"Runoff continuity error is {ro:.2f}% (above 1%).")
        for w in rpt.get("warnings", []):
            warnings.append(f"SWMM: {w}")
        if any(not c["passed"] for c in crosschecks):
            warnings.append("A native cross-check failed: normalised volumes disagree with the SWMM report.")
        norm = ctx.workspace.normalised_dir
        series = {
            "system": [
                {k: r[k] for k in ("t_s", "rainfall_mm_h", "runoff_cms", "flooding_cms", "outfall_cms")}
                for r in system
            ],
            "nodes": {
                nid: [[r["t_s"], round(r["depth_m"], 4), round(r["flooding_cms"], 5)] for r in ser]
                for nid, ser in nodes.items()
            },
            "links": {
                lid: [[r["t_s"], round(r["flow_cms"], 5), round(r["capacity"], 4)] for r in ser]
                for lid, ser in links.items()
            },
            "columns": {
                "nodes": ["t_s", "depth_m", "flooding_cms"],
                "links": ["t_s", "flow_cms", "capacity"],
            },
            "note": "Network results only: SWMM node flooding does not define a surface inundation extent.",
        }
        (norm / "series.json").write_text(json.dumps(series, separators=(",", ":")))
        files = [
            NormalisedFile(
                "series.json",
                norm / "series.json",
                "timeseries",
                "application/json",
                "System, node and link time series at the report step",
            )
        ]
        return NormalisedResult(
            metrics=metrics,
            files=files,
            warnings=warnings,
            crosschecks=crosschecks,
            summary={
                **prepared.summary,
                "report_step_s": applied.report_step_s,
                "continuity": {"runoff_pct": ro, "routing_pct": rc},
            },
            units={"flow": "m3/s", "depth": "m", "volume": "m3"},
        )


def _network_issues(m: DrainageNetworkModel) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    node_ids = m.node_ids()
    elev = (
        {j.id: j.invert_elev_m for j in m.junctions}
        | {o.id: o.invert_elev_m for o in m.outfalls}
        | {s.id: s.invert_elev_m for s in m.storage}
    )
    depth = {j.id: j.max_depth_m for j in m.junctions} | {s.id: s.max_depth_m for s in m.storage}
    all_ids = [*([j.id for j in m.junctions]), *[o.id for o in m.outfalls], *[s.id for s in m.storage]]
    if len(all_ids) != len(set(all_ids)):
        issues.append(
            issue("error", "duplicate_node", "Node ids must be unique across junctions, outfalls and storage")
        )
    g = Graph(directed=False)
    for n in node_ids:
        g.add_node(n)
    for x in m.conduits:
        if x.from_node not in node_ids or x.to_node not in node_ids:
            issues.append(
                issue(
                    "error",
                    "invalid_connection",
                    f"Conduit {x.id} connects unknown node(s)",
                    path=f"content.conduits[{x.id}]",
                )
            )
            continue
        if x.from_node == x.to_node:
            issues.append(
                issue("error", "invalid_connection", f"Conduit {x.id} starts and ends at the same node")
            )
        g.add_edge(x.from_node, x.to_node)
        for end, node, off in (("inlet", x.from_node, x.in_offset_m), ("outlet", x.to_node, x.out_offset_m)):
            if node in depth and off + x.geom1_m > depth[node] + 1e-6:
                issues.append(
                    issue(
                        "warning",
                        "crown_above_rim",
                        f"Conduit {x.id} {end} crown ({off + x.geom1_m:.2f} m) is above node {node} depth "
                        f"({depth[node]:.2f} m)",
                    )
                )
        if x.from_node in elev and x.to_node in elev:
            up = elev[x.from_node] + x.in_offset_m
            dn = elev[x.to_node] + x.out_offset_m
            if up < dn:
                issues.append(
                    issue(
                        "warning",
                        "adverse_slope",
                        f"Conduit {x.id} has an adverse slope (inlet invert {up:.3f} m < outlet invert {dn:.3f} m)",
                        hint="Check invert levels and their vertical reference.",
                    )
                )
        if x.shape == "FILLED_CIRCULAR" and not (0 < x.geom2_m < x.geom1_m):
            issues.append(
                issue(
                    "error",
                    "invalid_xsection",
                    f"Conduit {x.id}: filled depth must be between 0 and the diameter",
                )
            )
    for p in m.pumps:
        if p.from_node not in node_ids or p.to_node not in node_ids:
            issues.append(issue("error", "invalid_connection", f"Pump {p.id} connects unknown node(s)"))
        else:
            g.add_edge(p.from_node, p.to_node)
        if p.curve_id not in {c.id for c in m.pump_curves}:
            issues.append(
                issue("error", "unknown_curve", f"Pump {p.id} references unknown curve {p.curve_id}")
            )
    outfalls = [o.id for o in m.outfalls]
    for n in sorted(node_ids - set(outfalls)):
        if not g.reaches_any(n, outfalls):
            issues.append(
                issue(
                    "error",
                    "disconnected_node",
                    f"Node {n} has no connection to any outfall",
                    hint="Connect the node or remove it.",
                )
            )
    for s in m.subcatchments:
        if s.outlet not in node_ids and s.outlet not in {x.id for x in m.subcatchments}:
            issues.append(
                issue("error", "invalid_outlet", f"Subcatchment {s.id} drains to unknown outlet {s.outlet}")
            )
        if s.raingage not in m.raingage_series:
            issues.append(
                issue("error", "unknown_raingage", f"Subcatchment {s.id} uses unknown raingage {s.raingage}")
            )
    for j in m.junctions:
        if j.provenance.kind.value == "assumption":
            issues.append(
                issue(
                    "info",
                    "assumed_inverts",
                    "Some invert levels or pipe dimensions are explicit assumptions (hidden infrastructure "
                    "cannot be measured from surface imagery).",
                )
            )
            break
    return issues


def _node_crowns(m: DrainageNetworkModel) -> dict[str, float]:
    crown: dict[str, float] = defaultdict(float)
    for x in m.conduits:
        crown[x.from_node] = max(crown[x.from_node], x.in_offset_m + x.geom1_m)
        crown[x.to_node] = max(crown[x.to_node], x.out_offset_m + x.geom1_m)
    return dict(crown)


def _read_rows(path: Path) -> list[dict]:
    with open(path) as f:
        rows = []
        for r in csv.DictReader(f):
            rows.append({k: (v if k in ("node", "link", "subcatchment") else float(v)) for k, v in r.items()})
        return rows


def _read_series(path: Path, key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in _read_rows(path):
        out[r[key]].append(r)
    return dict(out)


_NUM = r"(-?\d+(?:\.\d+)?)"


def parse_report(text: str) -> dict:
    """Parse the parts of a SWMM 5.2 report used for native cross-checks."""
    res: dict = {"warnings": []}
    for ln in text.splitlines():
        if ln.strip().startswith("WARNING"):
            res["warnings"].append(ln.strip())
    sec = _section(text, "Runoff Quantity Continuity")
    if sec:
        if m := re.search(r"Total Precipitation \.+\s+" + _NUM, sec):
            res["precipitation_ha_m"] = float(m.group(1))
        if m := re.search(r"Surface Runoff \.+\s+" + _NUM, sec):
            res["surface_runoff_ha_m"] = float(m.group(1))
        if m := re.search(r"Continuity Error \(%\) \.+\s+" + _NUM, sec):
            res["runoff_continuity_pct"] = float(m.group(1))
    sec = _section(text, "Flow Routing Continuity")
    if sec:
        if m := re.search(r"Flooding Loss \.+\s+" + _NUM, sec):
            res["flooding_loss_ha_m"] = float(m.group(1))
        if m := re.search(r"Continuity Error \(%\) \.+\s+" + _NUM, sec):
            res["routing_continuity_pct"] = float(m.group(1))
    sec = _section(text, "Node Flooding Summary")
    if sec and "No nodes were flooded" not in sec:
        vols = {}
        for ln in sec.splitlines():
            parts = ln.split()
            if len(parts) >= 7 and re.fullmatch(r"-?\d+(\.\d+)?", parts[1] or ""):
                try:
                    vols[parts[0]] = float(parts[5])
                except ValueError:
                    continue
        res["node_flood_volume_ml"] = vols
    sec = _section(text, "Link Flow Summary")
    if sec:
        ratios = {}
        for ln in sec.splitlines():
            parts = ln.split()
            if len(parts) >= 8 and parts[1] == "CONDUIT":
                try:
                    ratios[parts[0]] = float(parts[6])
                except ValueError:
                    continue
        res["link_max_full_flow"] = ratios
    return res


def _section(text: str, title: str) -> str | None:
    i = text.find(title)
    if i < 0:
        return None
    rest = text[i + len(title) :]
    # sections are separated by lines of asterisks introducing the next title
    j = rest.find("\n  ***", 200)
    return rest[: j if j > 0 else len(rest)]


__all__ = [
    "Conduit",
    "SwmmAdapter",
    "apply_controls",
    "filled_circular_area_fraction",
    "parse_report",
    "write_inp",
]
