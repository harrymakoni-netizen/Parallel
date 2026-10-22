"""Specialist collision analysis model (Virtual CRASH / PC-Crash exchange).

Survey geometry supports a specialist model; aftermath imagery alone never
establishes a unique sequence or vehicle speed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..geometry import LineStringGeom, Position
from ..provenance import Provenance


class CollisionVehicle(BaseModel):
    id: str
    vehicle_record_id: str | None = None
    label: str
    mass_kg: float = Field(gt=0)
    length_m: float = Field(gt=0)
    width_m: float = Field(gt=0)
    wheelbase_m: float | None = None
    provenance: Provenance


class EvidenceTarget(BaseModel):
    """A measured position the specialist reconstruction is compared against."""

    id: str
    observation_id: str
    vehicle_id: str | None = None
    kind: Literal["rest_position", "point_of_impact_area", "tyre_mark_start", "tyre_mark_end", "debris_centroid"]
    position: Position
    heading_deg: float | None = None
    position_uncertainty_m: float = Field(gt=0)


class SurveyPoint(BaseModel):
    id: str
    label: str
    position: Position


class CollisionCaseModel(BaseModel):
    model_kind: Literal["collision"] = "collision"
    friction_low: float = Field(gt=0, le=1.5)
    friction_high: float = Field(gt=0, le=1.5)
    friction_provenance: Provenance
    road_edges: list[LineStringGeom] = Field(default_factory=list)
    survey_points: list[SurveyPoint] = Field(default_factory=list)
    vehicles: list[CollisionVehicle] = Field(min_length=1)
    targets: list[EvidenceTarget] = Field(default_factory=list)


class CollisionControls(BaseModel):
    kind: Literal["collision"] = "collision"
    hypothesis_id: str
    notes: str | None = None
