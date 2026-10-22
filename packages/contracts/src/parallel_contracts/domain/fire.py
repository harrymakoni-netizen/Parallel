"""Fire model (NIST FDS 6).

Fire and pedestrian models are kept separate; FDS+Evac (discontinued) is not
used. Material fire properties must be supplied with provenance, never inferred
from appearance.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, model_validator

from ..provenance import Provenance, ProvenanceKind


def _xb(v: list[float]) -> list[float]:
    if len(v) != 6:
        raise ValueError("XB must have 6 values [x0,x1,y0,y1,z0,z1]")
    if v[0] > v[1] or v[2] > v[3] or v[4] > v[5]:
        raise ValueError("XB bounds must be ordered (min <= max)")
    return v


XB = Annotated[list[float], AfterValidator(_xb)]  # [x0, x1, y0, y1, z0, z1]


class FdsMesh(BaseModel):
    id: str
    ijk: list[int] = Field(min_length=3, max_length=3)
    xb: XB


class FdsSurface(BaseModel):
    id: str
    hrrpua_kw_m2: float | None = Field(default=None, description="Heat release rate per unit area (burner)")
    ramp_up_s: float | None = Field(default=None, description="TAU_Q: t-squared ramp time to full HRR")
    color: str | None = None
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))


class FdsObstruction(BaseModel):
    id: str
    xb: XB
    surf_id: str = "INERT"


class FdsVent(BaseModel):
    id: str
    xb: XB
    surf_id: str = "OPEN"


class FdsDevice(BaseModel):
    id: str
    quantity: Literal["TEMPERATURE", "VISIBILITY", "SOOT VOLUME FRACTION", "VOLUME FRACTION", "VELOCITY"]
    xyz: list[float] = Field(min_length=3, max_length=3)
    spec_id: str | None = None


class FireModel(BaseModel):
    model_kind: Literal["fire"] = "fire"
    chid: str = Field(pattern=r"^[A-Za-z0-9_]{1,30}$")
    title: str
    t_end_s: float = Field(gt=0)
    fuel: Literal["PROPANE", "METHANE", "HEPTANE", "POLYURETHANE"] = "PROPANE"
    soot_yield: float = 0.01
    co_yield: float = 0.0
    meshes: list[FdsMesh] = Field(min_length=1)
    surfaces: list[FdsSurface] = Field(default_factory=list)
    obstructions: list[FdsObstruction] = Field(default_factory=list)
    vents: list[FdsVent] = Field(default_factory=list)
    devices: list[FdsDevice] = Field(default_factory=list)

    @model_validator(mode="after")
    def _surf_refs(self) -> FireModel:
        ids = {s.id for s in self.surfaces} | {"INERT", "OPEN", "MIRROR"}
        for o in [*self.obstructions, *self.vents]:
            if o.surf_id not in ids:
                raise ValueError(f"{o.id} references unknown SURF {o.surf_id}")
        return self


class FireControls(BaseModel):
    kind: Literal["fire"] = "fire"
    hrr_scale: float = Field(default=1.0, gt=0, le=10)
    closed_vents: list[str] = Field(default_factory=list)
    t_end_s: float | None = None
