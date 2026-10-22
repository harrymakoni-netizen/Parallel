"""Coordinate frames and transforms (spec section 12).

Conventions (also documented in docs/data-contracts.md):

* Canonical scene frame: right-handed, Z-up, metres.
* Transform matrices are 4x4, stored row-major, applied to column vectors:
  ``p_to = M @ [x, y, z, 1]^T``. A similarity transform is ``M = [[s*R, t], [0, 1]]``.
* Quaternions are Hamilton convention, stored ``[w, x, y, z]``.
* Geographic projects store a horizontal CRS (EPSG code of a *projected* CRS
  for metric work), the local origin in that CRS, the rotation of the local +X
  axis from projected East (counter-clockwise, degrees), and a vertical
  reference which may be explicitly ``"unknown"``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from .provenance import Provenance, Uncertainty

IDENTITY_4X4 = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]

MATRIX_CONVENTION = "row-major 4x4, column vectors: p_to = M @ [x, y, z, 1]"
QUATERNION_CONVENTION = "Hamilton, [w, x, y, z]"


class FrameKind(StrEnum):
    capture = "capture"
    canonical = "canonical"
    geographic = "geographic"
    engine = "engine"


class CoordinateFrame(BaseModel):
    id: str
    kind: FrameKind
    description: str | None = None
    handedness: str = "right"
    up_axis: str = "+Z"
    length_unit: str = "m"


class Transform(BaseModel):
    from_frame: str
    to_frame: str
    matrix: list[list[float]] = Field(default_factory=lambda: [row[:] for row in IDENTITY_4X4])
    convention: str = MATRIX_CONVENTION
    method: str | None = Field(default=None, description="How the transform was established.")
    residual_rms_m: float | None = None
    provenance: Provenance | None = None

    @field_validator("matrix")
    @classmethod
    def _shape(cls, m: list[list[float]]) -> list[list[float]]:
        if len(m) != 4 or any(len(r) != 4 for r in m):
            raise ValueError("transform matrix must be 4x4")
        if [round(v, 12) for v in m[3]] != [0.0, 0.0, 0.0, 1.0]:
            raise ValueError("transform must be affine (last row 0 0 0 1)")
        return m


class GeoReference(BaseModel):
    horizontal_crs: str = Field(description="Projected CRS for metric work, e.g. 'EPSG:32735'.")
    local_origin_projected: list[float] = Field(min_length=2, max_length=3)
    local_rotation_deg: float = Field(
        default=0.0, description="Rotation of local +X from projected East, CCW."
    )
    vertical_reference: str = Field(default="unknown", description="Vertical datum, or explicitly 'unknown'.")
    vertical_offset_m: float | None = Field(
        default=None, description="Height of local z=0 in the vertical reference, when known."
    )
    accuracy: Uncertainty | None = None
    provenance: Provenance | None = None

    @field_validator("horizontal_crs")
    @classmethod
    def _crs(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError("CRS must be an authority:code string, e.g. EPSG:32735")
        return v


class SceneCoordinates(BaseModel):
    canonical_frame: str = "canonical"
    frames: list[CoordinateFrame] = Field(
        default_factory=lambda: [CoordinateFrame(id="canonical", kind=FrameKind.canonical)]
    )
    transforms: list[Transform] = Field(default_factory=list)
    georeference: GeoReference | None = None
