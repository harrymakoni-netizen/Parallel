"""Building thermal/energy model (EnergyPlus, prepared with OpenStudio).

Geometry, constructions, systems, schedules and weather are required inputs;
none of them may be inferred as fact from a building's appearance, so each
carries provenance (defaulting to an explicit assumption).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..geometry import PolygonGeom
from ..provenance import Provenance, ProvenanceKind


def _assumed() -> Provenance:
    return Provenance(kind=ProvenanceKind.assumption)


class ThermalZoneDef(BaseModel):
    id: str
    name: str
    footprint: PolygonGeom
    floor_to_floor_m: float = Field(default=3.0, gt=0)
    num_floors: int = Field(default=1, ge=1, le=60)
    provenance: Provenance = Field(default_factory=_assumed)


class EnvelopeSpec(BaseModel):
    wall_u_w_m2k: float = Field(default=0.35, gt=0)
    roof_u_w_m2k: float = Field(default=0.25, gt=0)
    floor_u_w_m2k: float = Field(default=0.3, gt=0)
    window_u_w_m2k: float = Field(default=1.8, gt=0)
    window_shgc: float = Field(default=0.4, gt=0, lt=1)
    window_to_wall_ratio: float = Field(default=0.3, ge=0, lt=0.95)
    provenance: Provenance = Field(default_factory=_assumed)


class InternalLoads(BaseModel):
    people_per_m2: float = 0.05
    lighting_w_m2: float = 8.0
    equipment_w_m2: float = 10.0
    heating_setpoint_c: float = 20.0
    cooling_setpoint_c: float = 24.0
    provenance: Provenance = Field(default_factory=_assumed)


class DesignDay(BaseModel):
    name: str
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)
    day_type: Literal["SummerDesignDay", "WinterDesignDay"]
    max_dry_bulb_c: float
    daily_range_c: float = Field(ge=0)
    humidity_wet_bulb_c: float
    pressure_pa: float = 101325.0
    wind_speed_mps: float = 3.0
    provenance: Provenance = Field(default_factory=_assumed)


class ThermalModel(BaseModel):
    model_kind: Literal["thermal"] = "thermal"
    north_axis_deg: float = 0.0
    zones: list[ThermalZoneDef] = Field(min_length=1)
    envelope: EnvelopeSpec = Field(default_factory=EnvelopeSpec)
    loads: InternalLoads = Field(default_factory=InternalLoads)
    weather_asset_id: str | None = Field(default=None, description="EPW weather file asset for annual runs")
    design_days: list[DesignDay] = Field(default_factory=list, description="Design-day sizing periods")
    latitude_deg: float
    longitude_deg: float
    time_zone_h: float
    elevation_m: float = 0.0


class ThermalControls(BaseModel):
    kind: Literal["thermal"] = "thermal"
    wall_u_w_m2k: float | None = Field(default=None, gt=0)
    window_u_w_m2k: float | None = Field(default=None, gt=0)
    window_to_wall_ratio: float | None = Field(default=None, ge=0, lt=0.95)
    heating_setpoint_c: float | None = None
    cooling_setpoint_c: float | None = None
    lighting_w_m2: float | None = Field(default=None, ge=0)
    run: Literal["design_days", "annual"] = "design_days"
