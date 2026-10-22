"""Storm drainage network model (EPA SWMM 5.2).

Kept strictly separate from pressurised water supply (EPANET). All lengths and
elevations are metres in the model's vertical reference, which must be stated.
Hidden pipe dimensions and invert levels must be supplied or explicitly
assumed: every node and conduit carries provenance.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..geometry import PolygonGeom, Position
from ..provenance import Provenance, ProvenanceKind


def _assumed() -> Provenance:
    return Provenance(kind=ProvenanceKind.assumption)


class RainfallSeries(BaseModel):
    id: str
    name: str
    interval_min: float = Field(gt=0, description="Rain gage recording interval")
    intensities_mm_per_h: list[float] = Field(min_length=1, description="One intensity per interval, starting at t=0")
    provenance: Provenance = Field(default_factory=_assumed)

    @property
    def total_depth_mm(self) -> float:
        return sum(self.intensities_mm_per_h) * self.interval_min / 60.0


class HortonInfiltration(BaseModel):
    max_rate_mm_per_h: float = 76.2
    min_rate_mm_per_h: float = 12.7
    decay_per_h: float = 4.0
    dry_time_d: float = 7.0
    max_infil_mm: float = 0.0


class Subcatchment(BaseModel):
    id: str
    raingage: str
    outlet: str
    area_ha: float = Field(gt=0)
    imperv_pct: float = Field(ge=0, le=100)
    width_m: float = Field(gt=0)
    slope_pct: float = Field(gt=0)
    n_imperv: float = 0.012
    n_perv: float = 0.15
    dstore_imperv_mm: float = 1.5
    dstore_perv_mm: float = 5.0
    pct_zero_imperv: float = 25.0
    infiltration: HortonInfiltration = Field(default_factory=HortonInfiltration)
    polygon: PolygonGeom | None = None
    provenance: Provenance = Field(default_factory=_assumed)


class Junction(BaseModel):
    id: str
    position: Position
    invert_elev_m: float
    max_depth_m: float = Field(gt=0, description="Invert to rim (ground) depth")
    init_depth_m: float = 0.0
    surcharge_depth_m: float = 0.0
    ponded_area_m2: float = 0.0
    provenance: Provenance = Field(default_factory=_assumed)

    @property
    def rim_elev_m(self) -> float:
        return self.invert_elev_m + self.max_depth_m


class Outfall(BaseModel):
    id: str
    position: Position
    invert_elev_m: float
    type: Literal["FREE", "NORMAL", "FIXED"] = "FREE"
    fixed_stage_m: float | None = None
    provenance: Provenance = Field(default_factory=_assumed)


class StorageUnit(BaseModel):
    id: str
    position: Position
    invert_elev_m: float
    max_depth_m: float = Field(gt=0)
    init_depth_m: float = 0.0
    area_m2: float = Field(gt=0, description="Constant plan area (FUNCTIONAL shape with zero coefficient)")
    provenance: Provenance = Field(default_factory=_assumed)


ConduitShape = Literal["CIRCULAR", "FILLED_CIRCULAR", "RECT_CLOSED", "RECT_OPEN", "TRAPEZOIDAL"]


class Conduit(BaseModel):
    id: str
    from_node: str
    to_node: str
    length_m: float = Field(gt=0)
    roughness_n: float = Field(gt=0, description="Manning's n")
    in_offset_m: float = 0.0
    out_offset_m: float = 0.0
    shape: ConduitShape = "CIRCULAR"
    geom1_m: float = Field(gt=0, description="Diameter (circular) or full height")
    geom2_m: float = 0.0
    geom3: float = 0.0
    geom4: float = 0.0
    barrels: int = 1
    vertices: list[Position] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=_assumed)


class PumpCurve(BaseModel):
    id: str
    type: Literal["PUMP1", "PUMP2", "PUMP3", "PUMP4"] = "PUMP3"
    points: list[tuple[float, float]] = Field(min_length=1, description="(head m or depth m, flow m3/s) pairs")


class Pump(BaseModel):
    id: str
    from_node: str
    to_node: str
    curve_id: str
    initially_on: bool = True
    startup_depth_m: float = 0.0
    shutoff_depth_m: float = 0.0


class LidControl(BaseModel):
    """SWMM bio-retention cell (BC) layer parameters, SI units as written to [LID_CONTROLS]."""

    id: str
    type: Literal["BC"] = "BC"
    surface_storage_mm: float = 150.0
    surface_veg_fraction: float = 0.0
    surface_roughness: float = 0.1
    surface_slope_pct: float = 1.0
    soil_thickness_mm: float = 500.0
    soil_porosity: float = 0.5
    soil_field_capacity: float = 0.2
    soil_wilting_point: float = 0.1
    soil_ksat_mm_per_h: float = 50.0
    soil_conductivity_slope: float = 10.0
    soil_suction_head_mm: float = 80.0
    storage_thickness_mm: float = 300.0
    storage_void_ratio: float = 0.75
    storage_seepage_mm_per_h: float = 5.0
    storage_clogging: float = 0.0
    drain_coeff: float = 0.0
    drain_exponent: float = 0.5
    drain_offset_mm: float = 6.0
    provenance: Provenance = Field(default_factory=_assumed)


class LidUsage(BaseModel):
    subcatchment_id: str
    lid_id: str
    number: int = Field(ge=1)
    area_each_m2: float = Field(gt=0)
    width_m: float = 0.0
    init_saturation_pct: float = 0.0
    from_imperv_pct: float = Field(ge=0, le=100, description="% of impervious area runoff treated")
    to_pervious: bool = False


class DrainageNetworkModel(BaseModel):
    model_kind: Literal["drainage_network"] = "drainage_network"
    vertical_reference: str = Field(description="Vertical datum for all elevations; required for drainage models.")
    start_datetime: str = "01/01/2026 00:00:00"
    end_datetime: str = "01/01/2026 06:00:00"
    report_step_s: int = 60
    wet_step_s: int = 60
    dry_step_s: int = 3600
    routing_step_s: float = 5.0
    rainfall: list[RainfallSeries] = Field(min_length=1)
    raingage_series: dict[str, str] = Field(description="raingage id -> rainfall series id")
    subcatchments: list[Subcatchment] = Field(default_factory=list)
    junctions: list[Junction] = Field(default_factory=list)
    outfalls: list[Outfall] = Field(min_length=1)
    storage: list[StorageUnit] = Field(default_factory=list)
    conduits: list[Conduit] = Field(min_length=1)
    pump_curves: list[PumpCurve] = Field(default_factory=list)
    pumps: list[Pump] = Field(default_factory=list)
    lid_controls: list[LidControl] = Field(default_factory=list)
    lid_usage: list[LidUsage] = Field(default_factory=list)

    @model_validator(mode="after")
    def _vertical(self) -> DrainageNetworkModel:
        if not self.vertical_reference or self.vertical_reference.strip().lower() == "unknown":
            raise ValueError(
                "Drainage elevations and invert levels require a stated vertical reference (not 'unknown')."
            )
        series = {r.id for r in self.rainfall}
        for g, s in self.raingage_series.items():
            if s not in series:
                raise ValueError(f"raingage {g} references unknown rainfall series {s}")
        return self

    def node_ids(self) -> set[str]:
        return {n.id for n in self.junctions} | {n.id for n in self.outfalls} | {n.id for n in self.storage}


class ConduitBlockage(BaseModel):
    """Explicit hydraulic representation of a blockage.

    Maps to SWMM ``FILLED_CIRCULAR`` geometry with sediment depth
    ``fraction * diameter``. The equivalent blocked cross-section area fraction
    is reported alongside (depth fraction != area fraction).
    """

    conduit_id: str
    sediment_depth_fraction: float = Field(gt=0, lt=0.95)


class ConduitResize(BaseModel):
    conduit_id: str
    diameter_m: float = Field(gt=0)


class NodeStorageConversion(BaseModel):
    """Replace a junction by a storage unit at the same invert (explicit storage addition)."""

    junction_id: str
    area_m2: float = Field(gt=0)
    max_depth_m: float | None = Field(default=None, gt=0, description="Defaults to the junction depth")


class PumpControlChange(BaseModel):
    pump_id: str
    startup_depth_m: float
    shutoff_depth_m: float
    initially_on: bool | None = None


class CatchmentChange(BaseModel):
    subcatchment_id: str
    imperv_pct: float = Field(ge=0, le=100)


class DrainageControls(BaseModel):
    kind: Literal["drainage"] = "drainage"
    rainfall_series_id: str | None = Field(default=None, description="Use this series for every raingage")
    rainfall_scale: float = Field(default=1.0, gt=0, le=5)
    blockages: list[ConduitBlockage] = Field(default_factory=list)
    resizes: list[ConduitResize] = Field(default_factory=list)
    storage_conversions: list[NodeStorageConversion] = Field(default_factory=list)
    lid_controls: list[LidControl] = Field(default_factory=list)
    lid_usage: list[LidUsage] = Field(default_factory=list)
    pump_changes: list[PumpControlChange] = Field(default_factory=list)
    catchment_changes: list[CatchmentChange] = Field(default_factory=list)
