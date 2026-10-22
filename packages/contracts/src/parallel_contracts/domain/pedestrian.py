"""Pedestrian geometry model (JuPedSim).

JuPedSim's documented geometry is a connected planar walkable polygon with
excluded obstacles. Closed routes are represented explicitly as additional
excluded areas; disconnected components are handled by reachability
accounting rather than discarded.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..geometry import MultiPolygonGeom, PolygonGeom, Position
from ..provenance import Provenance, ProvenanceKind
from .common import Distribution, NamedPolygon


class ExitDef(BaseModel):
    id: str
    name: str
    kind: Literal["exit", "refuge"] = "exit"
    polygon: PolygonGeom
    capacity: int | None = Field(default=None, description="Refuge capacity (persons), if limited.")
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))


class StartGroup(BaseModel):
    id: str
    name: str
    zone: PolygonGeom
    count: int = Field(ge=0)
    positions: list[Position] | None = Field(
        default=None,
        description="Explicit start positions. When absent, positions are generated deterministically from the run seed.",
    )
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))

    @model_validator(mode="after")
    def _positions_count(self) -> StartGroup:
        if self.positions is not None and len(self.positions) != self.count:
            raise ValueError(f"start group {self.id}: {len(self.positions)} positions but count={self.count}")
        return self


class RouteSegment(BaseModel):
    """A named region of the walkable area which a scenario may close (it then becomes an excluded area)."""

    id: str
    name: str
    polygon: PolygonGeom
    description: str | None = None


class MovementParameters(BaseModel):
    model: Literal["CollisionFreeSpeedModel"] = "CollisionFreeSpeedModel"
    desired_speed: Distribution = Field(
        default_factory=lambda: Distribution(
            kind="normal", mean=1.2, sd=0.2, clip_low=0.5, clip_high=2.0, unit="m/s"
        )
    )
    radius: Distribution = Field(default_factory=lambda: Distribution.constant(0.2, "m"))
    time_gap_s: float = 1.0
    strength_neighbor_repulsion: float = 8.0
    range_neighbor_repulsion: float = 0.1
    strength_geometry_repulsion: float = 5.0
    range_geometry_repulsion: float = 0.02
    provenance: Provenance = Field(
        default_factory=lambda: Provenance(
            kind=ProvenanceKind.assumption,
            source="JuPedSim CollisionFreeSpeedModel documented defaults",
        )
    )


class PedestrianModel(BaseModel):
    model_kind: Literal["pedestrian_geometry"] = "pedestrian_geometry"
    walkable_area: PolygonGeom | MultiPolygonGeom
    obstacles: list[NamedPolygon] = Field(default_factory=list)
    exits: list[ExitDef] = Field(min_length=1)
    start_groups: list[StartGroup] = Field(default_factory=list)
    route_segments: list[RouteSegment] = Field(default_factory=list)
    movement: MovementParameters = Field(default_factory=MovementParameters)
    level_note: str = "Single approximately level section; multi-level transfers are not modelled."

    @model_validator(mode="after")
    def _unique_ids(self) -> PedestrianModel:
        for label, items in (
            ("exit", self.exits),
            ("start group", self.start_groups),
            ("route segment", self.route_segments),
            ("obstacle", self.obstacles),
        ):
            ids = [i.id for i in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} ids")
        return self


class PedestrianControls(BaseModel):
    kind: Literal["pedestrian"] = "pedestrian"
    closed_route_segments: list[str] = Field(default_factory=list, description="Unavailable from the start.")
    closed_exits: list[str] = Field(default_factory=list, description="Unavailable from the start.")
    population_overrides: dict[str, int] = Field(default_factory=dict, description="start group id -> count")
    exit_assignment: dict[str, list[str]] = Field(
        default_factory=dict,
        description="start group id -> permitted exit ids. Groups not listed use the nearest reachable open exit.",
    )
    alarm_delay_s: float = Field(default=0.0, ge=0)
    pre_movement: Distribution = Field(default_factory=lambda: Distribution.constant(0.0, "s"))
    desired_speed_override: Distribution | None = None
    max_duration_s: float = Field(default=900.0, gt=0)
    dt_s: float = Field(default=0.01, gt=0, le=0.1)
    trajectory_every_n_steps: int = Field(default=50, ge=1)
    timed_closures: list[dict] = Field(
        default_factory=list,
        description="Not supported: timed closures are disabled until their model behaviour is implemented and tested.",
    )

    @model_validator(mode="after")
    def _no_timed(self) -> PedestrianControls:
        if self.timed_closures:
            raise ValueError(
                "Timed closures are not enabled: their model behaviour has not been implemented and tested."
            )
        return self
