"""Discrete-event operations model (SimPy).

Explicit resources, processes and event logic. SimPy is discrete-event only: it
does not represent continuous fluid flow or spatial collisions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..provenance import Provenance, ProvenanceKind
from .common import Distribution


class OpsResource(BaseModel):
    id: str
    name: str
    kind: Literal["gate", "loading_bay", "crew", "equipment", "crane", "storage", "other"] = "other"
    capacity: int = Field(ge=1)
    spatial_object_id: str | None = None
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))


class OpsStep(BaseModel):
    id: str
    name: str
    resource_id: str | None = Field(default=None, description="Resource held for the step; None = no resource needed")
    duration: Distribution


class OpsArrival(BaseModel):
    kind: Literal["fixed_interval", "poisson", "schedule"]
    interval_s: float | None = None
    mean_interarrival_s: float | None = None
    times_s: list[float] | None = None
    start_s: float = 0.0
    end_s: float | None = None
    max_count: int | None = None

    @model_validator(mode="after")
    def _params(self) -> OpsArrival:
        if self.kind == "fixed_interval" and not self.interval_s:
            raise ValueError("fixed_interval arrivals need interval_s")
        if self.kind == "poisson" and not self.mean_interarrival_s:
            raise ValueError("poisson arrivals need mean_interarrival_s")
        if self.kind == "schedule" and not self.times_s:
            raise ValueError("schedule arrivals need times_s")
        return self


class OpsFlow(BaseModel):
    id: str
    name: str
    entity_type: str = "delivery"
    arrival: OpsArrival
    steps: list[OpsStep] = Field(min_length=1)
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))


class OperationsModel(BaseModel):
    model_kind: Literal["operations"] = "operations"
    resources: list[OpsResource] = Field(min_length=1)
    flows: list[OpsFlow] = Field(min_length=1)
    horizon_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _refs(self) -> OperationsModel:
        rids = {r.id for r in self.resources}
        if len(rids) != len(self.resources):
            raise ValueError("duplicate resource ids")
        for f in self.flows:
            for s in f.steps:
                if s.resource_id is not None and s.resource_id not in rids:
                    raise ValueError(f"flow {f.id} step {s.id} references unknown resource {s.resource_id}")
        return self


class OperationsControls(BaseModel):
    kind: Literal["operations"] = "operations"
    capacity_changes: dict[str, int] = Field(default_factory=dict, description="resource id -> capacity")
    added_resources: list[OpsResource] = Field(default_factory=list)
    step_resource_changes: dict[str, str] = Field(
        default_factory=dict, description="'flow_id.step_id' -> resource id (e.g. send deliveries to a second bay)"
    )
    arrival_interval_scale: float = Field(default=1.0, gt=0, le=10)
    replications: int = Field(default=10, ge=1, le=200)
