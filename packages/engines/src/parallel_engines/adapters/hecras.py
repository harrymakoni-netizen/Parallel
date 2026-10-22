"""HEC-RAS adapter (file exchange).

HEC-RAS 2D models are authored, run and mapped (RAS Mapper) in a Windows
workflow; Linux compute engines only run prepared plans. Parallel therefore
exchanges a documented package (terrain, perimeter, boundaries, hydrographs)
and imports exported maximum depth / velocity rasters. Imports must state the
terrain, mesh, boundaries, time reference and vertical datum.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.hydraulics2d import SurfaceHydraulicControls, SurfaceHydraulicModel
from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck
from parallel_contracts.metrics import MetricValue
from parallel_contracts.runs import ValidationReport
from parallel_contracts.scenario import ScenarioContent

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
from ..capabilities import HECRAS
from ..exchange import build_package, readme

REQUIRED_METADATA = (
    "hec_ras_version",
    "plan_name",
    "terrain",
    "mesh",
    "boundaries",
    "time_reference",
    "vertical_datum",
)


class HecRasAdapter(EngineAdapter):
    capability = HECRAS

    def default_mode(self) -> ExecutionMode:
        return ExecutionMode.file_exchange

    def check_installation(self, mode: ExecutionMode | None = None) -> InstallationCheck:
        mode = mode or ExecutionMode.file_exchange
        if mode == ExecutionMode.file_exchange:
            return self._check(
                mode,
                Availability.ready,
                details=[
                    "Exchange package + GeoTIFF import implemented; awaiting an operator execution record."
                ],
            )
        return self._check(
            mode,
            Availability.integration_incomplete,
            details=[
                "Managed execution of prepared plans with the Linux compute engines is not implemented."
            ],
            remedy="Use file exchange with a Windows HEC-RAS 6.6 workstation.",
        )

    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport:
        m = model.content
        if not isinstance(m, SurfaceHydraulicModel):
            return ValidationReport.from_issues(
                [issue("error", "wrong_model", "HEC-RAS requires a surface_hydraulics model")]
            )
        issues = []
        if not any(b.kind in ("flow_hydrograph", "stage_hydrograph", "precipitation") for b in m.boundaries):
            issues.append(
                issue("error", "no_inflow", "At least one inflow or precipitation boundary is required")
            )
        if not any(b.kind in ("normal_depth", "stage_hydrograph") for b in m.boundaries):
            issues.append(
                issue("warning", "no_outflow", "No outflow boundary (normal depth or stage) is defined")
            )
        for b in m.boundaries:
            if b.kind != "precipitation" and b.line is None:
                issues.append(issue("error", "boundary_line", f"Boundary {b.id} needs a boundary line"))
        return ValidationReport.from_issues(issues)

    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m, c = ctx.model.content, ctx.scenario.controls
        assert isinstance(m, SurfaceHydraulicModel) and isinstance(c, SurfaceHydraulicControls)
        d = ctx.workspace.input_dir
        notes = [f"Inflow hydrographs scaled by {c.inflow_scale:g}."] if c.inflow_scale != 1 else []
        feats = {
            "type": "FeatureCollection",
            "crs_note": f"Local metres; georeference {m.horizontal_crs}",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "2D flow area perimeter", "cell_size_m": m.cell_size_m},
                    "geometry": m.perimeter.model_dump(),
                }
            ]
            + [
                {
                    "type": "Feature",
                    "properties": {"id": b.id, "name": b.name, "kind": b.kind},
                    "geometry": b.line.model_dump(),
                }
                for b in m.boundaries
                if b.line
            ]
            + [
                {"type": "Feature", "properties": {"kind": "breakline"}, "geometry": bl.model_dump()}
                for bl in m.breaklines
            ]
            + [
                {
                    "type": "Feature",
                    "properties": {"id": r.id, "manning_n": c.roughness_overrides.get(r.id, r.manning_n)},
                    "geometry": r.polygon.model_dump(),
                }
                for r in m.roughness_regions
            ],
        }
        files = {"geometry.geojson": write_json(d / "geometry.geojson", feats)}
        hyd = d / "boundary_series.csv"
        with open(hyd, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["boundary_id", "kind", "hours", "value", "unit"])
            for b in m.boundaries:
                scale = c.inflow_scale if b.kind == "flow_hydrograph" else 1.0
                for h, v in b.series:
                    w.writerow([b.id, b.kind, h, v * scale, b.value_unit])
        files["boundary_series.csv"] = hyd
        files["plan.json"] = write_json(
            d / "plan.json",
            {
                "target_release": m.target_release,
                "simulation_start": m.simulation_start,
                "simulation_end": m.simulation_end,
                "computation_interval_s": m.computation_interval_s,
                "output_interval_s": m.output_interval_s,
                "default_manning_n": m.default_manning_n,
                "horizontal_crs": m.horizontal_crs,
                "vertical_datum": m.vertical_datum,
            },
        )
        if ctx.asset_path:
            files["terrain.tif"] = ctx.asset_path(m.terrain_asset_id)
        return PreparedInputs(
            files=files,
            hashes=hash_inputs(files),
            model_change_notes=notes,
            summary={"vertical_datum": m.vertical_datum, "time_reference": m.simulation_start},
        )

    def build_exchange_package(self, ctx: RunContext, prepared: PreparedInputs) -> Path:
        m = ctx.model.content
        assert isinstance(m, SurfaceHydraulicModel)
        text = readme(
            f"HEC-RAS 2D exchange package ({m.target_release})",
            [
                "Create a HEC-RAS project; in RAS Mapper set the projection to " + m.horizontal_crs + ".",
                "Import data/terrain.tif as the terrain (vertical datum: " + m.vertical_datum + ").",
                "Create the 2D flow area from the perimeter in data/geometry.geojson with the stated cell size; add breaklines.",
                "Assign roughness regions; create boundary conditions on the boundary lines with data/boundary_series.csv.",
                "Configure the unsteady plan with the times and intervals in data/plan.json and run it.",
                "Export maximum depth (and velocity) rasters from RAS Mapper as GeoTIFF in the same projection.",
                "Fill in metadata.json (see result template) and zip it with the rasters; upload to the waiting run.",
            ],
            {
                "geometry.geojson": "Perimeter, boundary lines, breaklines, roughness regions",
                "boundary_series.csv": "Hydrograph / stage series",
                "plan.json": "Plan timing, datum and CRS",
                "terrain.tif": "Terrain raster",
            },
            {
                "max_depth.tif": "Maximum depth raster (m)",
                "max_velocity.tif": "Maximum velocity raster (m/s), optional",
                "metadata.json": "Required keys: " + ", ".join(REQUIRED_METADATA),
            },
            [
                "Imported rasters are results of an external installation; Parallel records the operator and metadata."
            ],
        )
        return build_package(
            engine_id=self.engine_id,
            engine_name=self.capability.name,
            run_id=ctx.run_id,
            files=dict(prepared.files),
            readme=text,
            result_template={
                "required": ["max_depth.tif", "metadata.json"],
                "optional": ["max_velocity.tif"],
                "metadata_keys": list(REQUIRED_METADATA),
            },
            dest=ctx.workspace.root / "exchange" / f"{ctx.run_id}-hecras-package.zip",
        )

    def import_external_result(
        self, ctx: RunContext, package_dir: Path, options: dict[str, Any]
    ) -> NormalisedResult:
        import rasterio

        meta_p, depth_p = package_dir / "metadata.json", package_dir / "max_depth.tif"
        if not meta_p.exists() or not depth_p.exists():
            raise EngineError(
                "result package must contain metadata.json and max_depth.tif", deterministic=True
            )
        meta = json.loads(meta_p.read_text())
        missing = [k for k in REQUIRED_METADATA if not str(meta.get(k, "")).strip()]
        if missing:
            raise EngineError(f"metadata.json is missing required keys: {missing}", deterministic=True)
        if str(meta["vertical_datum"]).strip().lower() == "unknown":
            raise EngineError("vertical datum must be stated", deterministic=True)
        with rasterio.open(depth_p) as ds:
            band = ds.read(1, masked=True).astype(float)
            cell_area = abs(ds.transform.a * ds.transform.e)
            crs = ds.crs.to_string() if ds.crs else None
        vals = np.ma.compressed(band)
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            raise EngineError("max_depth.tif contains no valid cells", deterministic=True)
        metrics = [
            MetricValue(metric_id="hyd.max_depth", value=float(vals.max()), unit="m"),
            MetricValue(
                metric_id="hyd.inundated_area", value=float((vals > 0.1).sum() * cell_area), unit="m2"
            ),
        ]
        m = ctx.model.content
        warnings = []
        if isinstance(m, SurfaceHydraulicModel) and crs and m.horizontal_crs not in crs:
            warnings.append(f"Raster CRS {crs} differs from the model CRS {m.horizontal_crs}.")
        return NormalisedResult(
            metrics=metrics,
            warnings=warnings,
            units={"depth": "m", "area": "m2"},
            summary={"metadata": meta, "raster_crs": crs, "cell_area_m2": cell_area},
        )


def validate_scenario_kind(scenario: ScenarioContent) -> bool:
    return isinstance(scenario.controls, SurfaceHydraulicControls)
