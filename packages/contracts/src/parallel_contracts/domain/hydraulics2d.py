"""River / surface-flow hydraulic model (HEC-RAS 2D exchange).

Results must record terrain, mesh, boundaries, time reference and vertical
datum; a stated vertical datum is mandatory.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..geometry import LineStringGeom, PolygonGeom
from ..provenance import Provenance


class RoughnessRegion(BaseModel):
    id: str
    polygon: PolygonGeom
    manning_n: float = Field(gt=0)
    provenance: Provenance


class BoundaryCondition(BaseModel):
    id: str
    name: str
    kind: Literal["flow_hydrograph", "stage_hydrograph", "normal_depth", "precipitation"]
    line: LineStringGeom | None = None
    series: list[tuple[float, float]] = Field(default_factory=list, description="(hours, value)")
    value_unit: str = "m3/s"
    friction_slope: float | None = None
    provenance: Provenance


class SurfaceHydraulicModel(BaseModel):
    model_kind: Literal["surface_hydraulics"] = "surface_hydraulics"
    terrain_asset_id: str = Field(description="Terrain raster (GeoTIFF) asset")
    horizontal_crs: str
    vertical_datum: str
    perimeter: PolygonGeom
    cell_size_m: float = Field(gt=0)
    breaklines: list[LineStringGeom] = Field(default_factory=list)
    default_manning_n: float = Field(gt=0)
    roughness_regions: list[RoughnessRegion] = Field(default_factory=list)
    boundaries: list[BoundaryCondition] = Field(min_length=1)
    simulation_start: str = Field(description="ISO 8601 start time (time reference)")
    simulation_end: str
    computation_interval_s: float = Field(gt=0)
    output_interval_s: float = Field(gt=0)
    target_release: str = "HEC-RAS 6.6"

    @model_validator(mode="after")
    def _datum(self) -> SurfaceHydraulicModel:
        if self.vertical_datum.strip().lower() in ("", "unknown"):
            raise ValueError("A HEC-RAS model requires a stated vertical datum.")
        return self


class SurfaceHydraulicControls(BaseModel):
    kind: Literal["surface_hydraulics"] = "surface_hydraulics"
    inflow_scale: float = Field(default=1.0, gt=0, le=10)
    roughness_overrides: dict[str, float] = Field(default_factory=dict)
