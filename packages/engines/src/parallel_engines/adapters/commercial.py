"""Licensed vendor engines integrated through documented file exchange.

Ventsim, FLAC3D, 3DEC, Virtual CRASH and PC-Crash are commercial products
without assumed public execution APIs. Parallel exports neutral, hashed input
packages for a licensed operator and imports results only through templates
documented here. Availability is ``licence_required`` until an organisation
registers a licensed installation; PC-Crash result import stays
``integration_incomplete`` until vendor documentation is obtained and tested.
"""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any

from parallel_contracts.crashlens import PlaybackSource, TrajectoryColumnMapping, TrajectorySample
from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.collision import CollisionCaseModel
from parallel_contracts.domain.geomechanics import BlockModel, ContinuumModel
from parallel_contracts.domain.ventilation import VentilationNetwork
from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck
from parallel_contracts.metrics import MetricStatus, MetricValue
from parallel_contracts.runs import ValidationIssue, ValidationReport
from parallel_metrics.collision import evidence_fit, evidence_fit_metrics

from ..base import (
    EngineAdapter,
    EngineError,
    NormalisedResult,
    PreparedInputs,
    RunContext,
    hash_inputs,
    issue,
    write_json,
)
from ..capabilities import FLAC3D, PCCRASH, THREEDEC, VENTSIM, VIRTUALCRASH
from ..exchange import build_package, readme

MAX_RESULT_ROWS = 2_000_000


def _csv(path: Path, header: list[str], rows: list[list[Any]]) -> Path:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def read_template_csv(path: Path, required: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise EngineError(f"result file {path.name} is missing", deterministic=True)
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        missing = [c for c in required if c not in cols]
        if missing:
            raise EngineError(f"{path.name} is missing required columns {missing}", deterministic=True)
        rows = []
        for i, r in enumerate(reader):
            if i >= MAX_RESULT_ROWS:
                raise EngineError(f"{path.name} exceeds {MAX_RESULT_ROWS} rows", deterministic=True)
            rows.append(r)
    return rows


def dxf_lines(segments: list[tuple[list[float], list[float]]], layer: str = "ROAD_EDGE") -> str:
    """Minimal ASCII DXF (R12 entities section) containing LINE entities."""
    out = ["0", "SECTION", "2", "ENTITIES"]
    for a, b in segments:
        za = a[2] if len(a) > 2 else 0.0
        zb = b[2] if len(b) > 2 else 0.0
        out += [
            "0",
            "LINE",
            "8",
            layer,
            "10",
            f"{a[0]:.4f}",
            "20",
            f"{a[1]:.4f}",
            "30",
            f"{za:.4f}",
            "11",
            f"{b[0]:.4f}",
            "21",
            f"{b[1]:.4f}",
            "31",
            f"{zb:.4f}",
        ]
    out += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(out) + "\n"


class _ExchangeAdapter(EngineAdapter):
    """Common behaviour for licensed engines used via file exchange."""

    model_kinds: tuple[str, ...] = ()
    import_supported = True

    def default_mode(self) -> ExecutionMode:
        return ExecutionMode.file_exchange

    def check_installation(
        self, mode: ExecutionMode | None = None, installation: dict | None = None
    ) -> InstallationCheck:
        mode = mode or ExecutionMode.file_exchange
        if mode != ExecutionMode.file_exchange:
            return self._check(
                mode,
                Availability.integration_incomplete,
                details=[f"{self.capability.name}: only documented file exchange is supported."],
            )
        if not self.import_supported:
            return self._check(
                mode,
                Availability.integration_incomplete,
                details=[
                    "Scene package export available; result import not specified until vendor "
                    "documentation is obtained and tested."
                ],
            )
        if not installation or not installation.get("licence_confirmed"):
            return self._check(
                mode,
                Availability.licence_required,
                details=["No licensed installation registered for this organisation."],
                remedy="Register the licensed installation (edition, licence holder, operator) in Organisation "
                "Settings > Integrations.",
            )
        return self._check(
            mode,
            Availability.ready,
            version=installation.get("version"),
            details=[
                f"Licensed installation registered: {installation.get('edition', 'edition not stated')}",
                "Exchange templates not yet verified against vendor output.",
            ],
        )

    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport:
        if model.content.model_kind not in self.model_kinds:
            return ValidationReport.from_issues(
                [issue("error", "wrong_model", f"{self.capability.name} requires one of {self.model_kinds}")]
            )
        return ValidationReport.from_issues(self._model_issues(model))

    def _model_issues(self, model: DomainModelEnvelope) -> list[ValidationIssue]:
        return []

    def _package(
        self,
        ctx: RunContext,
        prepared: PreparedInputs,
        title: str,
        steps: list[str],
        files_doc: dict[str, str],
        results_doc: dict[str, str],
        notes: list[str],
        template: dict,
    ) -> Path:
        text = readme(title, steps, files_doc, results_doc, notes + self.capability.limitations)
        return build_package(
            engine_id=self.engine_id,
            engine_name=self.capability.name,
            run_id=ctx.run_id,
            files=dict(prepared.files),
            readme=text,
            result_template=template,
            dest=ctx.workspace.root / "exchange" / f"{ctx.run_id}-{self.engine_id}-package.zip",
        )


def _prov_issues(items: list[tuple[str, Any]]) -> list[ValidationIssue]:
    out = []
    for label, prov in items:
        if prov.kind.value == "ai_suggested" and not prov.adopted_by:
            out.append(issue("error", "unadopted", f"{label}: unadopted AI suggestion"))
        if prov.kind.value in ("assumption", "synthetic", "algorithm_estimate"):
            out.append(
                issue(
                    "warning",
                    "specialist_input",
                    f"{label} is not measured or imported from site investigation ({prov.kind.value}). "
                    "Geomechanical properties require specialist inputs.",
                )
            )
    return out


class VentsimAdapter(_ExchangeAdapter):
    capability = VENTSIM
    model_kinds = ("ventilation_network",)

    def _model_issues(self, model):
        m = model.content
        assert isinstance(m, VentilationNetwork)
        ids = {j.id for j in m.junctions}
        out = [
            issue("error", "invalid_connection", f"Airway {a.id} connects unknown junction(s)")
            for a in m.airways
            if a.from_junction not in ids or a.to_junction not in ids
        ]
        aw = {a.id for a in m.airways}
        out += [
            issue("error", "unknown_airway", f"Fan {f.id} is on unknown airway {f.airway_id}")
            for f in m.fans
            if f.airway_id not in aw
        ]
        return out

    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m = ctx.model.content
        assert isinstance(m, VentilationNetwork)
        d = ctx.workspace.input_dir
        closed = set(getattr(ctx.scenario.controls, "closed_airways", []))
        files = {
            "junctions.csv": _csv(
                d / "junctions.csv",
                ["junction_id", "x_m", "y_m", "z_m"],
                [[j.id, *j.position[:3], *([0.0] * (3 - len(j.position)))][:4] for j in m.junctions],
            ),
            "airways.csv": _csv(
                d / "airways.csv",
                [
                    "airway_id",
                    "from",
                    "to",
                    "length_m",
                    "area_m2",
                    "perimeter_m",
                    "friction_factor_kg_m3",
                    "resistance_ns2_m8",
                    "closed",
                ],
                [
                    [
                        a.id,
                        a.from_junction,
                        a.to_junction,
                        a.length_m,
                        a.area_m2,
                        a.perimeter_m,
                        a.friction_factor_kg_m3 or "",
                        f"{a.atkinson_resistance:.6g}",
                        int(a.id in closed),
                    ]
                    for a in m.airways
                ],
            ),
            "fans.csv": _csv(
                d / "fans.csv",
                ["fan_id", "airway_id", "airflow_m3s", "pressure_pa"],
                [[f.id, f.airway_id, q, p] for f in m.fans for q, p in f.curve],
            ),
        }
        return PreparedInputs(
            files=files,
            hashes=hash_inputs(files),
            model_change_notes=[f"Airway {a} closed." for a in sorted(closed)],
        )

    def build_exchange_package(self, ctx: RunContext, prepared: PreparedInputs) -> Path:
        return self._package(
            ctx,
            prepared,
            "Ventsim exchange package",
            [
                "Build or update the Ventsim network from data/junctions.csv, data/airways.csv and data/fans.csv "
                "(Parallel neutral tables; resistance column computed with Atkinson's equation R = kLP/A³).",
                "Run the ventilation simulation in Ventsim.",
                "Export airway results and fill airway_results.csv using the template columns.",
                "Zip airway_results.csv with metadata.json (ventsim_edition, version, operator, notes) and upload.",
            ],
            {
                "junctions.csv": "Junction coordinates (m)",
                "airways.csv": "Airway geometry and resistance",
                "fans.csv": "Fan curve points",
            },
            {
                "airway_results.csv": "airway_id, airflow_m3s, pressure_drop_pa",
                "metadata.json": "ventsim_edition, version, operator",
            },
            ["The neutral tables are not a native Ventsim format; the operator maps them."],
            {
                "required": ["airway_results.csv", "metadata.json"],
                "airway_results.csv": ["airway_id", "airflow_m3s", "pressure_drop_pa"],
            },
        )

    def import_external_result(
        self, ctx: RunContext, package_dir: Path, options: dict[str, Any]
    ) -> NormalisedResult:
        m = ctx.model.content
        assert isinstance(m, VentilationNetwork)
        rows = read_template_csv(
            package_dir / "airway_results.csv", ["airway_id", "airflow_m3s", "pressure_drop_pa"]
        )
        known = {a.id for a in m.airways}
        unknown = [r["airway_id"] for r in rows if r["airway_id"] not in known]
        if unknown:
            raise EngineError(f"result references unknown airways: {unknown[:5]}", deterministic=True)
        fan_aw = {f.airway_id for f in m.fans}
        total = sum(abs(float(r["airflow_m3s"])) for r in rows if r["airway_id"] in fan_aw)
        return NormalisedResult(
            metrics=[MetricValue(metric_id="vent.total_airflow", value=total, unit="m3/s")],
            units={"airflow": "m3/s", "pressure": "Pa"},
        )


class _ItascaAdapter(_ExchangeAdapter):
    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m = ctx.model.content
        d = ctx.workspace.input_dir
        files = {
            "model.json": write_json(d / "model.json", m.model_dump(mode="json")),
            "monitoring_points.csv": _csv(
                d / "monitoring_points.csv",
                ["point_id", "x_m", "y_m", "z_m", "quantity"],
                [[p.id, *p.position[:3], p.quantity] for p in m.monitoring],
            ),
        }  # type: ignore[union-attr]
        stages = getattr(ctx.scenario.controls, "active_stages", [])
        notes = [f"Excavation stages applied in order: {', '.join(stages)}."] if stages else []
        return PreparedInputs(files=files, hashes=hash_inputs(files), model_change_notes=notes)

    def build_exchange_package(self, ctx: RunContext, prepared: PreparedInputs) -> Path:
        return self._package(
            ctx,
            prepared,
            f"{self.capability.name} exchange package",
            [
                f"A geotechnical specialist builds the {self.capability.name} model from data/model.json "
                "(geometry, materials with provenance, in-situ stress (compression negative), stages).",
                "Record displacements at the monitoring points for each stage.",
                "Fill monitoring_results.csv (point_id, stage_id, ux_m, uy_m, uz_m) and metadata.json "
                "(software_version, operator, constitutive assumptions, convergence notes); zip and upload.",
            ],
            {
                "model.json": "Neutral model definition",
                "monitoring_points.csv": "Monitoring point locations (m)",
            },
            {
                "monitoring_results.csv": "point_id, stage_id, ux_m, uy_m, uz_m",
                "metadata.json": "software_version, operator",
            },
            [
                "No command script is generated: command syntax has not been verified against a licensed installation."
            ],
            {
                "required": ["monitoring_results.csv", "metadata.json"],
                "monitoring_results.csv": ["point_id", "stage_id", "ux_m", "uy_m", "uz_m"],
            },
        )

    def import_external_result(
        self, ctx: RunContext, package_dir: Path, options: dict[str, Any]
    ) -> NormalisedResult:
        rows = read_template_csv(
            package_dir / "monitoring_results.csv", ["point_id", "stage_id", "ux_m", "uy_m", "uz_m"]
        )
        pts = {p.id for p in ctx.model.content.monitoring}  # type: ignore[union-attr]
        if any(r["point_id"] not in pts for r in rows):
            raise EngineError("result references unknown monitoring points", deterministic=True)
        mags = [math.sqrt(sum(float(r[k]) ** 2 for k in ("ux_m", "uy_m", "uz_m"))) for r in rows]
        if not mags:
            raise EngineError("monitoring_results.csv has no rows", deterministic=True)
        return NormalisedResult(
            metrics=[MetricValue(metric_id="geomech.max_displacement", value=max(mags), unit="m")],
            units={"displacement": "m"},
        )


class FLAC3DAdapter(_ItascaAdapter):
    capability = FLAC3D
    model_kinds = ("geomech_continuum",)

    def _model_issues(self, model):
        m = model.content
        assert isinstance(m, ContinuumModel)
        out = _prov_issues(
            [(f"Material {x.id}", x.provenance) for x in m.materials]
            + [("In-situ stress", m.stress.provenance)]
        )
        ids = {x.id for x in m.materials}
        if m.default_material_id not in ids:
            out.append(
                issue("error", "unknown_material", f"Default material {m.default_material_id} not defined")
            )
        for x in m.materials:
            if x.constitutive_model == "mohr-coulomb" and (x.cohesion_pa is None or x.friction_deg is None):
                out.append(
                    issue(
                        "error",
                        "missing_strength",
                        f"Material {x.id}: Mohr-Coulomb needs cohesion and friction",
                    )
                )
        return out


class ThreeDECAdapter(_ItascaAdapter):
    capability = THREEDEC
    model_kinds = ("geomech_discrete",)

    def _model_issues(self, model):
        m = model.content
        assert isinstance(m, BlockModel)
        return _prov_issues(
            [("Block material", m.block_material.provenance), ("In-situ stress", m.stress.provenance)]
            + [(f"Joint set {j.id}", j.provenance) for j in m.joint_sets]
        )

    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        prep = super().prepare_inputs(ctx)
        m = ctx.model.content
        assert isinstance(m, BlockModel)
        p = _csv(
            ctx.workspace.input_dir / "joint_sets.csv",
            [
                "joint_set_id",
                "dip_deg",
                "dip_direction_deg",
                "spacing_m",
                "friction_deg",
                "cohesion_pa",
                "tension_pa",
                "kn_pa_m",
                "ks_pa_m",
            ],
            [
                [
                    j.id,
                    j.dip_deg,
                    j.dip_direction_deg,
                    j.spacing_m,
                    j.friction_deg,
                    j.cohesion_pa,
                    j.tension_pa,
                    j.normal_stiffness_pa_m,
                    j.shear_stiffness_pa_m,
                ]
                for j in m.joint_sets
            ],
        )
        prep.files["joint_sets.csv"] = p
        prep.hashes = hash_inputs(prep.files)
        return prep


class _CollisionAdapter(_ExchangeAdapter):
    model_kinds = ("collision",)

    def _model_issues(self, model):
        m = model.content
        assert isinstance(m, CollisionCaseModel)
        out = []
        if m.friction_low > m.friction_high:
            out.append(issue("error", "friction_range", "Friction low bound exceeds high bound"))
        if not m.targets:
            out.append(issue("warning", "no_targets", "No evidence targets: residuals cannot be computed"))
        out.append(issue("info", "no_fault", "Parallel does not determine fault or legal admissibility."))
        return out

    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m = ctx.model.content
        assert isinstance(m, CollisionCaseModel)
        d = ctx.workspace.input_dir
        segs = []
        for e in m.road_edges:
            segs += [(a, b) for a, b in zip(e.coordinates, e.coordinates[1:], strict=False)]
        (d / "road_edges.dxf").write_text(dxf_lines(segs))
        files = {
            "survey_points.csv": _csv(
                d / "survey_points.csv",
                ["point_id", "label", "x_m", "y_m", "z_m"],
                [[p.id, p.label, *(list(p.position) + [0.0] * 3)[:3]] for p in m.survey_points],
            ),
            "road_edges.dxf": d / "road_edges.dxf",
            "vehicles.csv": _csv(
                d / "vehicles.csv",
                ["vehicle_id", "label", "mass_kg", "length_m", "width_m", "wheelbase_m", "source"],
                [
                    [
                        v.id,
                        v.label,
                        v.mass_kg,
                        v.length_m,
                        v.width_m,
                        v.wheelbase_m or "",
                        v.provenance.source or v.provenance.kind.value,
                    ]
                    for v in m.vehicles
                ],
            ),
            "evidence_targets.csv": _csv(
                d / "evidence_targets.csv",
                [
                    "target_id",
                    "observation_id",
                    "vehicle_id",
                    "kind",
                    "x_m",
                    "y_m",
                    "heading_deg",
                    "uncertainty_m",
                ],
                [
                    [
                        t.id,
                        t.observation_id,
                        t.vehicle_id or "",
                        t.kind,
                        t.position[0],
                        t.position[1],
                        "" if t.heading_deg is None else t.heading_deg,
                        t.position_uncertainty_m,
                    ]
                    for t in m.targets
                ],
            ),
            "case_model.json": write_json(d / "case_model.json", m.model_dump(mode="json")),
        }
        hyp = getattr(ctx.scenario.controls, "hypothesis_id", None)
        return PreparedInputs(files=files, hashes=hash_inputs(files), summary={"hypothesis_id": hyp})


class VirtualCrashAdapter(_CollisionAdapter):
    capability = VIRTUALCRASH

    def build_exchange_package(self, ctx: RunContext, prepared: PreparedInputs) -> Path:
        return self._package(
            ctx,
            prepared,
            "Virtual CRASH exchange package (CrashLens)",
            [
                "Import data/road_edges.dxf and data/survey_points.csv into the Virtual CRASH scene (metres, local frame).",
                "Set up vehicles from data/vehicles.csv and the friction range in data/case_model.json.",
                f"Model the hypothesis {prepared.summary.get('hypothesis_id')} as described in the case.",
                "Export the simulation data as text (time, position x/y, heading, speed per vehicle) to trajectories.csv.",
                "Upload a zip with trajectories.csv and metadata.json (software_version, operator, notes); confirm the "
                "column mapping in Parallel.",
            ],
            {
                "survey_points.csv": "Surveyed points",
                "road_edges.dxf": "Road edges (DXF LINE entities)",
                "vehicles.csv": "Vehicle parameters with sources",
                "evidence_targets.csv": "Measured evidence targets",
                "case_model.json": "Neutral case model",
            },
            {
                "trajectories.csv": "Exported simulation data (columns mapped on import)",
                "metadata.json": "software_version, operator",
            },
            [
                "Parallel computes evidence residuals for the imported trajectory; it does not determine fault."
            ],
            {
                "required": ["trajectories.csv", "metadata.json"],
                "column_mapping": TrajectoryColumnMapping.model_json_schema(),
            },
        )

    def import_external_result(
        self, ctx: RunContext, package_dir: Path, options: dict[str, Any]
    ) -> NormalisedResult:
        m = ctx.model.content
        assert isinstance(m, CollisionCaseModel)
        mapping = TrajectoryColumnMapping.model_validate(options.get("column_mapping") or {})
        trajs = parse_trajectory_csv(
            (package_dir / "trajectories.csv").read_text(encoding="utf-8-sig"), mapping
        )
        unknown = set(trajs) - {v.id for v in m.vehicles}
        if unknown:
            raise EngineError(
                f"trajectory vehicles not in the case model: {sorted(unknown)}; map them explicitly",
                deterministic=True,
            )
        hyp = options.get("hypothesis_id") or getattr(ctx.scenario.controls, "hypothesis_id", "unknown")
        fit = evidence_fit(hyp, PlaybackSource.imported_reconstruction, trajs, m.targets)
        metrics = evidence_fit_metrics(fit)
        playback = {
            "kind": "vehicles",
            "source": PlaybackSource.imported_reconstruction.value,
            "hypothesis_id": hyp,
            "vehicles": {vid: [s.model_dump() for s in samples] for vid, samples in trajs.items()},
        }
        norm = ctx.workspace.normalised_dir
        norm.mkdir(parents=True, exist_ok=True)
        (norm / "evidence_fit.json").write_text(fit.model_dump_json(indent=2))
        (norm / "playback.json").write_text(json.dumps(playback))
        from ..base import NormalisedFile

        return NormalisedResult(
            metrics=metrics,
            units={"position": "m", "speed": "m/s"},
            files=[
                NormalisedFile(
                    "evidence_fit.json",
                    norm / "evidence_fit.json",
                    "normalised",
                    "application/json",
                    "Defined residuals between the imported trajectory and measured evidence",
                ),
                NormalisedFile(
                    "playback.json",
                    norm / "playback.json",
                    "trajectory",
                    "application/json",
                    "Imported external reconstruction (labelled as such in playback)",
                ),
            ],
            summary={
                "hypothesis_id": hyp,
                "playback_source": PlaybackSource.imported_reconstruction.value,
                "targets_total": fit.targets_total,
            },
        )


class PCCrashAdapter(_CollisionAdapter):
    capability = PCCRASH
    import_supported = False

    def build_exchange_package(self, ctx: RunContext, prepared: PreparedInputs) -> Path:
        return self._package(
            ctx,
            prepared,
            "PC-Crash scene package (CrashLens)",
            [
                "Import the survey points and road edges into PC-Crash using its documented import functions.",
                "Result import into Parallel is not yet specified: obtain and test vendor documentation first.",
            ],
            {
                "survey_points.csv": "Surveyed points",
                "road_edges.dxf": "Road edges (DXF LINE entities)",
                "vehicles.csv": "Vehicle parameters",
                "evidence_targets.csv": "Measured evidence targets",
                "case_model.json": "Neutral case model",
            },
            {},
            ["Result import disabled until vendor documentation is obtained and tested."],
            {"required": [], "status": "integration_incomplete"},
        )

    def import_external_result(
        self, ctx: RunContext, package_dir: Path, options: dict[str, Any]
    ) -> NormalisedResult:
        raise EngineError(
            "PC-Crash result import is not specified: vendor documentation has not been obtained and tested.",
            deterministic=True,
            code="integration_incomplete",
        )


def parse_trajectory_csv(text: str, mapping: TrajectoryColumnMapping) -> dict[str, list[TrajectorySample]]:
    from parallel_contracts.units import convert

    reader = csv.DictReader(io.StringIO(text), delimiter=mapping.delimiter)
    cols = reader.fieldnames or []
    needed = [mapping.vehicle_column, mapping.time_column, mapping.x_column, mapping.y_column]
    needed += [c for c in (mapping.heading_column, mapping.speed_column) if c]
    missing = [c for c in needed if c not in cols]
    if missing:
        raise EngineError(
            f"trajectory file is missing mapped columns {missing}; found {cols}", deterministic=True
        )
    out: dict[str, list[TrajectorySample]] = {}
    for i, r in enumerate(reader):
        if i >= MAX_RESULT_ROWS:
            raise EngineError("trajectory file too large", deterministic=True)
        label = r[mapping.vehicle_column]
        vid = mapping.vehicle_id_map.get(label, label)
        try:
            x = convert(float(r[mapping.x_column]), mapping.length_unit, "m")
            y = convert(float(r[mapping.y_column]), mapping.length_unit, "m")
            h = (
                float(r[mapping.heading_column])
                if mapping.heading_column and r[mapping.heading_column] != ""
                else None
            )
            if h is not None and mapping.heading_unit == "rad":
                h = math.degrees(h)
            s = (
                float(r[mapping.speed_column])
                if mapping.speed_column and r[mapping.speed_column] != ""
                else None
            )
            if s is not None:
                s = convert(s, mapping.speed_unit, "m/s")
            out.setdefault(vid, []).append(
                TrajectorySample(t_s=float(r[mapping.time_column]), x=x, y=y, heading_deg=h, speed_mps=s)
            )
        except ValueError as exc:
            raise EngineError(f"row {i + 2}: non-numeric value ({exc})", deterministic=True) from exc
    for samples in out.values():
        samples.sort(key=lambda s: s.t_s)
    if not out:
        raise EngineError("trajectory file has no rows", deterministic=True)
    return out


__all__ = [
    "FLAC3DAdapter",
    "PCCrashAdapter",
    "ThreeDECAdapter",
    "VentsimAdapter",
    "VirtualCrashAdapter",
    "MetricStatus",
    "parse_trajectory_csv",
]
