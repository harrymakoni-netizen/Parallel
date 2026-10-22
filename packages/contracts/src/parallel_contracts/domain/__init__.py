"""Common envelope with specialised domain model schemas (spec section 12.4)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from .. import SCHEMA_VERSION
from ..provenance import Assumption, Provenance, ProvenanceKind
from .cfd import CfdControls, CfdStudyModel
from .collision import CollisionCaseModel, CollisionControls
from .drainage import DrainageControls, DrainageNetworkModel
from .fire import FireControls, FireModel
from .geomechanics import BlockModel, ContinuumModel, GeomechControls
from .hydraulics2d import SurfaceHydraulicControls, SurfaceHydraulicModel
from .operations import OperationsControls, OperationsModel
from .pedestrian import PedestrianControls, PedestrianModel
from .rigid_body import RigidBodyControls, RigidBodyScene
from .road import RoadNetworkModel, TrafficControls
from .thermal import ThermalControls, ThermalModel
from .ventilation import VentilationControls, VentilationNetwork
from .water_supply import WaterNetworkModel, WaterSupplyControls


class Discipline(StrEnum):
    pedestrian = "pedestrian"
    traffic = "traffic"
    drainage = "drainage"
    water_supply = "water_supply"
    operations = "operations"
    fire = "fire"
    cfd = "cfd"
    thermal = "thermal"
    geomechanics = "geomechanics"
    ventilation = "ventilation"
    surface_hydraulics = "surface_hydraulics"
    rigid_body = "rigid_body"
    collision = "collision"


DomainModelContent = Annotated[
    PedestrianModel
    | RoadNetworkModel
    | DrainageNetworkModel
    | WaterNetworkModel
    | OperationsModel
    | FireModel
    | CfdStudyModel
    | ThermalModel
    | ContinuumModel
    | BlockModel
    | VentilationNetwork
    | SurfaceHydraulicModel
    | RigidBodyScene
    | CollisionCaseModel,
    Field(discriminator="model_kind"),
]

MODEL_KIND_DISCIPLINE: dict[str, Discipline] = {
    "pedestrian_geometry": Discipline.pedestrian,
    "road_network": Discipline.traffic,
    "drainage_network": Discipline.drainage,
    "water_supply": Discipline.water_supply,
    "operations": Discipline.operations,
    "fire": Discipline.fire,
    "cfd_wind2d": Discipline.cfd,
    "thermal": Discipline.thermal,
    "geomech_continuum": Discipline.geomechanics,
    "geomech_discrete": Discipline.geomechanics,
    "ventilation_network": Discipline.ventilation,
    "surface_hydraulics": Discipline.surface_hydraulics,
    "rigid_body": Discipline.rigid_body,
    "collision": Discipline.collision,
}

ScenarioControls = Annotated[
    PedestrianControls
    | TrafficControls
    | DrainageControls
    | WaterSupplyControls
    | OperationsControls
    | FireControls
    | CfdControls
    | ThermalControls
    | GeomechControls
    | VentilationControls
    | SurfaceHydraulicControls
    | RigidBodyControls
    | CollisionControls,
    Field(discriminator="kind"),
]

CONTROLS_DISCIPLINE: dict[str, Discipline] = {
    "pedestrian": Discipline.pedestrian,
    "traffic": Discipline.traffic,
    "drainage": Discipline.drainage,
    "water_supply": Discipline.water_supply,
    "operations": Discipline.operations,
    "fire": Discipline.fire,
    "cfd": Discipline.cfd,
    "thermal": Discipline.thermal,
    "geomechanics": Discipline.geomechanics,
    "ventilation": Discipline.ventilation,
    "surface_hydraulics": Discipline.surface_hydraulics,
    "rigid_body": Discipline.rigid_body,
    "collision": Discipline.collision,
}


class DomainModelEnvelope(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION  # type: ignore[assignment]
    discipline: Discipline
    name: str
    description: str | None = None
    coordinate_frame: str = "canonical"
    scene_revision_id: str | None = Field(default=None, description="Scene revision the model was prepared from")
    content: DomainModelContent
    assumptions: list[Assumption] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))

    @model_validator(mode="after")
    def _discipline_matches(self) -> DomainModelEnvelope:
        expected = MODEL_KIND_DISCIPLINE[self.content.model_kind]
        if expected != self.discipline:
            raise ValueError(f"model kind {self.content.model_kind} belongs to discipline {expected}, not {self.discipline}")
        return self


__all__ = [
    "CONTROLS_DISCIPLINE",
    "MODEL_KIND_DISCIPLINE",
    "Discipline",
    "DomainModelContent",
    "DomainModelEnvelope",
    "ScenarioControls",
]
