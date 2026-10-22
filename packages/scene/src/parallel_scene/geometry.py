"""Planar geometry operations and validation (Shapely).

Used before simulation to reject self-intersecting walkable polygons, compute
walkable areas with excluded obstacles/closures, find disconnected components
and check object positions against model bounds.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.prepared import prep
from shapely.validation import explain_validity

from parallel_contracts.geometry import MultiPolygonGeom, PolygonGeom
from parallel_contracts.runs import ValidationIssue


def to_shapely(geom: PolygonGeom | MultiPolygonGeom | dict) -> Polygon | MultiPolygon:
    data = geom if isinstance(geom, dict) else geom.model_dump()
    rings = data["coordinates"]
    if data["type"] == "Polygon":
        return Polygon([p[:2] for p in rings[0]], [[p[:2] for p in h] for h in rings[1:]])
    if data["type"] == "MultiPolygon":
        return MultiPolygon(
            [Polygon([p[:2] for p in poly[0]], [[p[:2] for p in h] for h in poly[1:]]) for poly in rings]
        )
    return shape(data)


def from_shapely(poly: Polygon) -> PolygonGeom:
    ext = [list(c) for c in poly.exterior.coords]
    holes = [[list(c) for c in r.coords] for r in poly.interiors]
    return PolygonGeom(coordinates=[ext, *holes])


def validate_polygon(geom: PolygonGeom | MultiPolygonGeom, path: str, label: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    poly = to_shapely(geom)
    if not poly.is_valid:
        reason = explain_validity(poly)
        code = (
            "self_intersection"
            if "Self-intersection" in reason or "Ring Self" in reason
            else "invalid_polygon"
        )
        issues.append(
            ValidationIssue(
                severity="error",
                code=code,
                message=f"{label} is not a valid polygon: {reason}",
                path=path,
                hint="Edit the polygon so its boundary does not cross itself.",
            )
        )
    elif poly.area <= 0:
        issues.append(
            ValidationIssue(severity="error", code="zero_area", message=f"{label} has zero area", path=path)
        )
    return issues


@dataclass
class WalkableResult:
    area: Polygon | MultiPolygon
    components: list[Polygon]


def walkable_area(
    base: PolygonGeom | MultiPolygonGeom, exclusions: Iterable[PolygonGeom | MultiPolygonGeom]
) -> WalkableResult:
    area = to_shapely(base)
    excl = [to_shapely(e) for e in exclusions]
    if excl:
        area = area.difference(shapely.union_all(excl))
    # Remove slivers produced by boolean operations (smaller than 1 cm²)
    comps = [g for g in _polygons(area) if g.area > 1e-4]
    if len(comps) == 1:
        area = comps[0]
    elif comps:
        area = MultiPolygon(comps)
    return WalkableResult(area=area, components=sorted(comps, key=lambda p: -p.area))


def _polygons(g: shapely.Geometry) -> list[Polygon]:
    if isinstance(g, Polygon):
        return [] if g.is_empty else [g]
    if hasattr(g, "geoms"):
        out: list[Polygon] = []
        for sub in g.geoms:
            out.extend(_polygons(sub))
        return out
    return []


def component_index(components: Sequence[Polygon], point: Sequence[float], tol: float = 1e-6) -> int | None:
    p = Point(point[0], point[1])
    for i, c in enumerate(components):
        if c.covers(p) or c.distance(p) <= tol:
            return i
    return None


def polygon_component_overlap(components: Sequence[Polygon], geom: PolygonGeom) -> list[int]:
    """Indices of components that intersect a (destination) polygon with positive area."""
    g = to_shapely(geom)
    return [i for i, c in enumerate(components) if c.intersection(g).area > 1e-6]


def sample_points_in_polygon(
    poly: Polygon, n: int, rng: np.random.Generator, min_spacing: float, max_attempts: int = 200_000
) -> list[tuple[float, float]]:
    """Deterministic (given rng) rejection sampling with a minimum spacing between points."""
    minx, miny, maxx, maxy = poly.bounds
    pts: list[tuple[float, float]] = []
    attempts = 0
    prepared = prep(poly)
    inner = poly.buffer(-min_spacing / 2) if poly.buffer(-min_spacing / 2).area > 0 else poly
    prepared_inner = prep(inner)
    while len(pts) < n and attempts < max_attempts:
        attempts += 1
        x, y = rng.uniform(minx, maxx), rng.uniform(miny, maxy)
        if not prepared_inner.contains(Point(x, y)) or not prepared.contains(Point(x, y)):
            continue
        if all((x - px) ** 2 + (y - py) ** 2 >= min_spacing**2 for px, py in pts):
            pts.append((x, y))
    if len(pts) < n:
        raise ValueError(
            f"could only place {len(pts)} of {n} people with {min_spacing} m spacing in a {poly.area:.1f} m² zone"
        )
    return pts


def check_within_bounds(
    positions: Iterable[tuple[str, Sequence[float]]], bounds: Polygon | MultiPolygon, label: str
) -> list[ValidationIssue]:
    issues = []
    for oid, pos in positions:
        if not bounds.buffer(1e-6).covers(Point(pos[0], pos[1])):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="out_of_bounds",
                    message=f"{label} {oid} at ({pos[0]:.2f}, {pos[1]:.2f}) lies outside the model bounds",
                    path=oid,
                )
            )
    return issues
