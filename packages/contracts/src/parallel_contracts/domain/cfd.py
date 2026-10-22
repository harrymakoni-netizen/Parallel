"""Approved OpenFOAM case templates.

A general OpenFOAM installation is not a usable study workflow on its own; only
these templates (with their mesh checks and boundary-condition validation) are
executed by Parallel.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..provenance import Provenance, ProvenanceKind


class CfdObstacle(BaseModel):
    id: str
    name: str
    x0: float
    x1: float
    y0: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> CfdObstacle:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError(f"obstacle {self.id}: bounds must be ordered and non-degenerate")
        return self


class CfdStudyModel(BaseModel):
    """Template ``wind2d_obstacles``: steady incompressible 2D plan-view flow (simpleFoam, k-epsilon).

    The flow is resolved in a horizontal slice; vertical effects are not modelled.
    """

    model_kind: Literal["cfd_wind2d"] = "cfd_wind2d"
    template: Literal["wind2d_obstacles"] = "wind2d_obstacles"
    domain_x0: float
    domain_x1: float
    domain_y0: float
    domain_y1: float
    obstacles: list[CfdObstacle] = Field(default_factory=list)
    inlet_speed_mps: float = Field(gt=0, le=60)
    kinematic_viscosity_m2_s: float = 1.5e-5
    turbulence_intensity: float = Field(default=0.05, gt=0, lt=0.5)
    turbulent_length_scale_m: float = Field(default=1.0, gt=0)
    target_cell_size_m: float = Field(gt=0)
    max_cells: int = 60000
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))

    @model_validator(mode="after")
    def _domain(self) -> CfdStudyModel:
        if self.domain_x1 <= self.domain_x0 or self.domain_y1 <= self.domain_y0:
            raise ValueError("domain bounds must be ordered")
        for o in self.obstacles:
            if not (
                self.domain_x0 < o.x0 < o.x1 < self.domain_x1
                and self.domain_y0 < o.y0 < o.y1 < self.domain_y1
            ):
                raise ValueError(f"obstacle {o.id} must lie strictly inside the domain")
        return self


class CfdControls(BaseModel):
    kind: Literal["cfd"] = "cfd"
    inlet_speed_mps: float | None = Field(default=None, gt=0, le=60)
    removed_obstacles: list[str] = Field(default_factory=list)
    added_obstacles: list[CfdObstacle] = Field(default_factory=list)
    iterations: int = Field(default=600, ge=50, le=5000)
    comfort_threshold_mps: float = Field(
        default=5.0, gt=0, description="Speed threshold for the exceedance-area metric"
    )
