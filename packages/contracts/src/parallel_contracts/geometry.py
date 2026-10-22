"""Geometry in the canonical local frame (metres, right-handed, Z-up).

GeoJSON-like structures, but in local metric coordinates rather than
longitude/latitude. Geographic coordinates are only ever related to these via
an explicit GeoReference transform.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

Position = list[float]  # [x, y] or [x, y, z] in metres


def _check_position(p: list[float]) -> list[float]:
    if len(p) not in (2, 3):
        raise ValueError("positions must have 2 or 3 coordinates")
    for c in p:
        if c != c or c in (float("inf"), float("-inf")):
            raise ValueError("positions must be finite")
    return p


class PointGeom(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: Position

    @field_validator("coordinates")
    @classmethod
    def _v(cls, v: Position) -> Position:
        return _check_position(v)


class LineStringGeom(BaseModel):
    type: Literal["LineString"] = "LineString"
    coordinates: list[Position] = Field(min_length=2)

    @field_validator("coordinates")
    @classmethod
    def _v(cls, v: list[Position]) -> list[Position]:
        return [_check_position(p) for p in v]


class PolygonGeom(BaseModel):
    """Exterior ring followed by optional hole rings. Rings must be closed."""

    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[Position]] = Field(min_length=1)

    @field_validator("coordinates")
    @classmethod
    def _v(cls, rings: list[list[Position]]) -> list[list[Position]]:
        for ring in rings:
            if len(ring) < 4:
                raise ValueError("polygon rings need at least 4 positions (closed)")
            for p in ring:
                _check_position(p)
            if ring[0][:2] != ring[-1][:2]:
                raise ValueError("polygon rings must be closed (first position == last position)")
        return rings


class MultiPolygonGeom(BaseModel):
    type: Literal["MultiPolygon"] = "MultiPolygon"
    coordinates: list[list[list[Position]]] = Field(min_length=1)

    @field_validator("coordinates")
    @classmethod
    def _v(cls, polys: list[list[list[Position]]]) -> list[list[list[Position]]]:
        for rings in polys:
            PolygonGeom(coordinates=rings)
        return polys


class BoxGeom(BaseModel):
    """Simplified solid: oriented box. ``center`` is the box centroid; yaw about +Z."""

    type: Literal["Box"] = "Box"
    center: Position
    size: list[float] = Field(min_length=3, max_length=3)
    yaw_deg: float = 0.0

    @field_validator("size")
    @classmethod
    def _positive(cls, v: list[float]) -> list[float]:
        if any(s <= 0 for s in v):
            raise ValueError("box size components must be positive")
        return v


Geometry = Annotated[
    PointGeom | LineStringGeom | PolygonGeom | MultiPolygonGeom | BoxGeom,
    Field(discriminator="type"),
]


def rect(x0: float, y0: float, x1: float, y1: float) -> PolygonGeom:
    """Axis-aligned rectangle polygon helper (counter-clockwise exterior)."""
    return PolygonGeom(coordinates=[[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]])
