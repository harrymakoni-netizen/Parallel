"""Mine ventilation network (Ventsim exchange).

Ventilation properties (resistance, friction factor, fan curves) are kept
separate from the captured tunnel appearance.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..geometry import Position
from ..provenance import Provenance, ProvenanceKind


class VentJunction(BaseModel):
    id: str
    position: Position


class Airway(BaseModel):
    id: str
    from_junction: str
    to_junction: str
    length_m: float = Field(gt=0)
    area_m2: float = Field(gt=0)
    perimeter_m: float = Field(gt=0)
    friction_factor_kg_m3: float | None = Field(default=None, gt=0, description="Atkinson k")
    resistance_ns2_m8: float | None = Field(default=None, gt=0)
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))

    @model_validator(mode="after")
    def _resistance_source(self) -> Airway:
        if self.friction_factor_kg_m3 is None and self.resistance_ns2_m8 is None:
            raise ValueError(f"airway {self.id}: provide a friction factor or a resistance")
        return self

    @property
    def atkinson_resistance(self) -> float:
        """R = k * L * P / A^3 (Atkinson), when no explicit resistance is given."""
        if self.resistance_ns2_m8 is not None:
            return self.resistance_ns2_m8
        assert self.friction_factor_kg_m3 is not None
        return self.friction_factor_kg_m3 * self.length_m * self.perimeter_m / self.area_m2**3


class Fan(BaseModel):
    id: str
    airway_id: str
    curve: list[tuple[float, float]] = Field(min_length=2, description="(airflow m3/s, pressure Pa)")


class VentilationNetwork(BaseModel):
    model_kind: Literal["ventilation_network"] = "ventilation_network"
    junctions: list[VentJunction] = Field(min_length=2)
    airways: list[Airway] = Field(min_length=1)
    fans: list[Fan] = Field(default_factory=list)
    air_density_kg_m3: float = 1.2


class VentilationControls(BaseModel):
    kind: Literal["ventilation"] = "ventilation"
    closed_airways: list[str] = Field(default_factory=list)
    fan_off: list[str] = Field(default_factory=list)
