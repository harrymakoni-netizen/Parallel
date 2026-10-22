"""Local canonical <-> projected <-> geographic coordinates.

Metric distances are never computed from longitude/latitude: the georeference
must name a *projected* CRS, and local metres relate to it by a rotation about
Z plus the local origin offset.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from pyproj import CRS, Transformer

from parallel_contracts.coordinates import GeoReference


class GeoReferenceError(ValueError):
    pass


def check_projected(crs_code: str) -> CRS:
    try:
        crs = CRS.from_user_input(crs_code)
    except Exception as exc:  # pyproj raises CRSError
        raise GeoReferenceError(f"Unknown CRS '{crs_code}'") from exc
    if not crs.is_projected:
        raise GeoReferenceError(
            f"{crs_code} is not a projected CRS. Local metric work requires a projected CRS "
            "(distances must not be computed from longitude/latitude)."
        )
    axis_units = {a.unit_name for a in crs.axis_info[:2]}
    if not axis_units <= {"metre", "meter"}:
        raise GeoReferenceError(f"{crs_code} horizontal axes are in {axis_units}, expected metres")
    return crs


def local_to_projected(geo: GeoReference, xy: np.ndarray | Sequence[Sequence[float]]) -> np.ndarray:
    pts = np.atleast_2d(np.asarray(xy, dtype=float))[:, :2]
    th = math.radians(geo.local_rotation_deg)
    rot = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    origin = np.asarray(geo.local_origin_projected[:2], dtype=float)
    return pts @ rot.T + origin


def projected_to_local(geo: GeoReference, en: np.ndarray | Sequence[Sequence[float]]) -> np.ndarray:
    pts = np.atleast_2d(np.asarray(en, dtype=float))[:, :2]
    th = math.radians(geo.local_rotation_deg)
    rot = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    origin = np.asarray(geo.local_origin_projected[:2], dtype=float)
    return (pts - origin) @ rot


def local_to_lonlat(geo: GeoReference, xy: np.ndarray | Sequence[Sequence[float]]) -> np.ndarray:
    crs = check_projected(geo.horizontal_crs)
    t = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    en = local_to_projected(geo, xy)
    lon, lat = t.transform(en[:, 0], en[:, 1])
    return np.column_stack([lon, lat])


def lonlat_to_local(geo: GeoReference, lonlat: np.ndarray | Sequence[Sequence[float]]) -> np.ndarray:
    crs = check_projected(geo.horizontal_crs)
    t = Transformer.from_crs(CRS.from_epsg(4326), crs, always_xy=True)
    ll = np.atleast_2d(np.asarray(lonlat, dtype=float))
    e, n = t.transform(ll[:, 0], ll[:, 1])
    return projected_to_local(geo, np.column_stack([e, n]))


def utm_crs_for(lon: float, lat: float) -> str:
    """Suggest the WGS84 UTM zone CRS for a location (a projected CRS in metres)."""
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"
