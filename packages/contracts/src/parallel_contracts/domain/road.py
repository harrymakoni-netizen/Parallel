"""Road network + demand model (SUMO).

Map geometry does not provide calibrated demand: demand flows carry their own
provenance and default to an explicit assumption.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..geometry import Position
from ..provenance import Provenance, ProvenanceKind

NodeType = Literal["priority", "traffic_light", "right_before_left", "allway_stop", "dead_end", "priority_stop"]


class RoadNode(BaseModel):
    id: str
    x: float
    y: float
    type: NodeType = "priority"


class RoadEdge(BaseModel):
    id: str
    from_node: str
    to_node: str
    num_lanes: int = Field(default=1, ge=1, le=8)
    speed_mps: float = Field(default=13.89, gt=0)
    priority: int = 1
    name: str | None = None
    shape: list[Position] | None = Field(default=None, description="Intermediate shape points (local metres).")
    allow_pedestrians_sidewalk: bool = False
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))


class TlsPhase(BaseModel):
    duration_s: float = Field(gt=0)
    state: str = Field(description="SUMO signal state string, one character per controlled link (rRgGyYoO).")


class TlsProgram(BaseModel):
    node_id: str
    program_id: str = "parallel"
    offset_s: float = 0.0
    phases: list[TlsPhase] = Field(min_length=1)


class VehicleType(BaseModel):
    id: str
    accel: float = 2.6
    decel: float = 4.5
    sigma: float = 0.5
    length: float = 5.0
    min_gap: float = 2.5
    max_speed: float = 55.55
    vclass: str = "passenger"


class DemandFlow(BaseModel):
    id: str
    from_edge: str
    to_edge: str
    begin_s: float = 0.0
    end_s: float = 3600.0
    number: int = Field(ge=0, description="Exact number of vehicles inserted evenly between begin and end.")
    vtype: str = "car"
    provenance: Provenance = Field(
        default_factory=lambda: Provenance(kind=ProvenanceKind.assumption, note="Uncalibrated demand assumption")
    )

    @model_validator(mode="after")
    def _interval(self) -> DemandFlow:
        if self.end_s <= self.begin_s:
            raise ValueError(f"flow {self.id}: end_s must be after begin_s")
        return self


class RoadNetworkModel(BaseModel):
    model_kind: Literal["road_network"] = "road_network"
    nodes: list[RoadNode] = Field(min_length=2)
    edges: list[RoadEdge] = Field(min_length=1)
    tls_programs: list[TlsProgram] = Field(default_factory=list)
    vtypes: list[VehicleType] = Field(default_factory=lambda: [VehicleType(id="car")])
    demand: list[DemandFlow] = Field(default_factory=list)
    step_length_s: float = Field(default=0.5, gt=0, le=1.0)

    @model_validator(mode="after")
    def _refs(self) -> RoadNetworkModel:
        node_ids = {n.id for n in self.nodes}
        edge_ids = {e.id for e in self.edges}
        if len(node_ids) != len(self.nodes):
            raise ValueError("duplicate node ids")
        if len(edge_ids) != len(self.edges):
            raise ValueError("duplicate edge ids")
        for e in self.edges:
            if e.from_node not in node_ids or e.to_node not in node_ids:
                raise ValueError(f"edge {e.id} references unknown node")
        vt = {v.id for v in self.vtypes}
        for f in self.demand:
            if f.from_edge not in edge_ids or f.to_edge not in edge_ids:
                raise ValueError(f"flow {f.id} references unknown edge")
            if f.vtype not in vt:
                raise ValueError(f"flow {f.id} references unknown vehicle type {f.vtype}")
        return self


class TrafficControls(BaseModel):
    kind: Literal["traffic"] = "traffic"
    closed_edges: list[str] = Field(default_factory=list)
    lane_changes: dict[str, int] = Field(default_factory=dict, description="edge id -> number of lanes")
    speed_changes_mps: dict[str, float] = Field(default_factory=dict)
    node_type_changes: dict[str, NodeType] = Field(default_factory=dict, description="Intersection control alternatives")
    tls_programs: list[TlsProgram] = Field(default_factory=list, description="Replacement signal programs")
    demand_scale: float = Field(default=1.0, gt=0, le=5.0)
    end_s: float = Field(default=3600.0, gt=0, description="Simulation horizon")
    warmup_s: float = Field(default=0.0, ge=0, description="Trips departing before this time are excluded from metrics")
    fcd_period_s: float = Field(default=1.0, gt=0)
