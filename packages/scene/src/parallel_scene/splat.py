"""Validation of Gaussian splat PLY files trained outside Parallel, and a check that one sits in the
same coordinate frame as the sparse reconstruction it claims to come from.

A trained splat is appearance only. These checks establish that the file is a well-formed splat and
that it is registered to the reconstruction; they say nothing about geometry, scale or measurement
quality, which stay governed by the scene's own alignment and quality states.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import ply

MAX_GAUSSIANS = 15_000_000
_CHUNK = 1_000_000
_CHECKED = ("x", "y", "z", "opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3")


class SplatError(ValueError):
    pass


@dataclass
class SplatInfo:
    count: int
    sh_degree: int
    bytes_per_gaussian: int
    opacity_sigmoid_median: float
    notes: list[str] = field(default_factory=list)


def _memmap(path, header: ply.PlyHeader) -> np.ndarray:
    dtype = np.dtype([(n, "<" + t) for n, t in header.properties])
    expected = header.header_bytes + header.vertex_count * dtype.itemsize
    size = _file_size(path)
    if size != expected:
        raise SplatError(
            f"the file is {size} bytes but its header describes {expected} bytes "
            f"({header.vertex_count} Gaussians); it is truncated or has trailing data"
        )
    return np.memmap(path, dtype=dtype, mode="r", offset=header.header_bytes, shape=(header.vertex_count,))


def _file_size(path) -> int:
    from pathlib import Path

    return Path(path).stat().st_size


def validate(path) -> tuple[SplatInfo, np.ndarray]:
    """Check a binary Gaussian splat PLY and return its summary and the Gaussian centres (N, 3)."""
    try:
        header = ply.read_header(path)
    except ply.PlyError as exc:
        raise SplatError(str(exc)) from exc
    if header.format != "binary_little_endian":
        raise SplatError("only binary little-endian PLY files are accepted for trained splats")
    if not header.is_gaussian_splat:
        missing = sorted(ply.GAUSSIAN_REQUIRED - {p for p, _ in header.properties})
        raise SplatError(f"the PLY is not a Gaussian splat: missing properties {', '.join(missing)}")
    if header.vertex_count == 0:
        raise SplatError("the splat contains no Gaussians")
    if header.vertex_count > MAX_GAUSSIANS:
        raise SplatError(f"the splat has {header.vertex_count} Gaussians, above the limit of {MAX_GAUSSIANS}")
    types = dict(header.properties)
    if any(types[n] != "f4" for n in _CHECKED):
        raise SplatError("position, opacity, scale and rotation properties must be 32-bit floats")
    mm = _memmap(path, header)
    for lo in range(0, header.vertex_count, _CHUNK):
        part = mm[lo : lo + _CHUNK]
        for name in _CHECKED:
            if not np.isfinite(part[name]).all():
                raise SplatError(f"the splat contains non-finite values in '{name}'")
    xyz = np.stack([np.asarray(mm["x"]), np.asarray(mm["y"]), np.asarray(mm["z"])], axis=1).astype(np.float64)
    n_rest = sum(1 for p, _ in header.properties if p.startswith("f_rest_"))
    sh_degree = {0: 0, 9: 1, 24: 2, 45: 3}.get(n_rest, -1)
    notes = []
    if sh_degree < 0:
        notes.append(f"unusual number of view-dependent colour coefficients ({n_rest})")
    step = max(1, header.vertex_count // 200_000)
    opacity = 1.0 / (1.0 + np.exp(-np.asarray(mm["opacity"][::step], dtype=np.float64)))
    return (
        SplatInfo(
            count=header.vertex_count,
            sh_degree=max(sh_degree, 0),
            bytes_per_gaussian=mm.dtype.itemsize,
            opacity_sigmoid_median=float(np.median(opacity)),
            notes=notes,
        ),
        xyz,
    )


def alignment_check(
    splat_xyz: np.ndarray,
    sfm_xyz: np.ndarray,
    *,
    max_relative_distance: float = 0.05,
    spread_ratio_range: tuple[float, float] = (0.5, 2.0),
    sample: int = 20_000,
) -> dict:
    """Compare a splat with the sparse points of the reconstruction, in the capture frame.

    Trained Gaussians cluster on the surfaces the sparse points were triangulated on, so the median
    distance from a sparse point to its nearest Gaussian centre must be small relative to the extent of
    the scene, and the two clouds must have a similar robust spread. Both statistics use medians so that
    floaters and background Gaussians do not dominate.
    """
    from scipy.spatial import cKDTree

    sfm = np.asarray(sfm_xyz, dtype=np.float64)
    if len(sfm) < 20:
        raise SplatError("the reconstruction has too few sparse points to check the splat's alignment")
    splat = np.asarray(splat_xyz, dtype=np.float64)
    rng = np.random.default_rng(0)
    if len(splat) > 500_000:
        splat = splat[rng.choice(len(splat), 500_000, replace=False)]
    if len(sfm) > sample:
        sfm = sfm[rng.choice(len(sfm), sample, replace=False)]
    sfm_spread = float(np.median(np.linalg.norm(sfm - np.median(sfm, axis=0), axis=1)))
    splat_spread = float(np.median(np.linalg.norm(splat - np.median(splat, axis=0), axis=1)))
    if sfm_spread <= 0:
        raise SplatError("the sparse points are degenerate (zero spread)")
    dist, _ = cKDTree(splat).query(sfm)
    median_rel = float(np.median(dist)) / sfm_spread
    ratio = splat_spread / sfm_spread
    lo, hi = spread_ratio_range
    ok = median_rel <= max_relative_distance and lo <= ratio <= hi
    return {
        "passed": bool(ok),
        "median_distance_fraction_of_spread": round(median_rel, 5),
        "p90_distance_fraction_of_spread": round(float(np.percentile(dist, 90)) / sfm_spread, 5),
        "spread_ratio_splat_to_sparse": round(ratio, 4),
        "scene_spread_capture_units": round(sfm_spread, 5),
        "sparse_points_compared": int(len(sfm)),
        "gaussians_compared": int(len(splat)),
        "limits": {"max_median_distance_fraction": max_relative_distance, "spread_ratio_range": [lo, hi]},
    }
