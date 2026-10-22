"""Pressurised water distribution model (EPA EPANET 2.2).

Separate from storm drainage (SWMM): different physics, schema and engine.
Flow units are litres per second; headloss is Hazen-Williams.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..geometry import Position
from ..provenance import Provenance, ProvenanceKind


def _assumed() -> Provenance:
    return Provenance(kind=ProvenanceKind.assumption)


class WaterJunction(BaseModel):
    id: str
    position: Position
    elevation_m: float
    base_demand_lps: float = 0.0
    pattern_id: str | None = None
    provenance: Provenance = Field(default_factory=_assumed)


class WaterReservoir(BaseModel):
    id: str
    position: Position
    head_m: float
    provenance: Provenance = Field(default_factory=_assumed)


class WaterTank(BaseModel):
    id: str
    position: Position
    elevation_m: float
    init_level_m: float
    min_level_m: float
    max_level_m: float
    diameter_m: float = Field(gt=0)
    provenance: Provenance = Field(default_factory=_assumed)

    @model_validator(mode="after")
    def _levels(self) -> WaterTank:
        if not (self.min_level_m <= self.init_level_m <= self.max_level_m):
            raise ValueError(f"tank {self.id}: require min <= init <= max level")
        return self


class WaterPipe(BaseModel):
    id: str
    node1: str
    node2: str
    length_m: float = Field(gt=0)
    diameter_mm: float = Field(gt=0)
    roughness_hw: float = Field(default=130.0, gt=0, description="Hazen-Williams C")
    minor_loss: float = 0.0
    status: Literal["OPEN", "CLOSED", "CV"] = "OPEN"
    provenance: Provenance = Field(default_factory=_assumed)


class WaterPump(BaseModel):
    id: str
    node1: str
    node2: str
    curve_points: list[tuple[float, float]] = Field(
        min_length=1, description="(flow L/s, head m) pump curve points"
    )
    speed: float = 1.0


class DemandPattern(BaseModel):
    id: str
    multipliers: list[float] = Field(min_length=1)


class WaterNetworkModel(BaseModel):
    model_kind: Literal["water_supply"] = "water_supply"
    vertical_reference: str
    junctions: list[WaterJunction] = Field(default_factory=list)
    reservoirs: list[WaterReservoir] = Field(default_factory=list)
    tanks: list[WaterTank] = Field(default_factory=list)
    pipes: list[WaterPipe] = Field(min_length=1)
    pumps: list[WaterPump] = Field(default_factory=list)
    patterns: list[DemandPattern] = Field(default_factory=list)
    duration_s: int = 24 * 3600
    hydraulic_step_s: int = 3600
    pattern_step_s: int = 3600
    report_step_s: int = 3600

    @model_validator(mode="after")
    def _sources(self) -> WaterNetworkModel:
        if not self.reservoirs and not self.tanks:
            raise ValueError("a water network needs at least one reservoir or tank (fixed-head source)")
        return self


class WaterSupplyControls(BaseModel):
    kind: Literal["water_supply"] = "water_supply"
    closed_pipes: list[str] = Field(default_factory=list)
    demand_multiplier: float = Field(default=1.0, gt=0, le=10)
    extra_demand_lps: dict[str, float] = Field(
        default_factory=dict, description="junction id -> added demand (e.g. fire flow)"
    )
    pipe_diameter_changes_mm: dict[str, float] = Field(default_factory=dict)
    min_pressure_m: float = Field(
        default=15.0, description="Service pressure threshold used for the deficiency metric"
    )
