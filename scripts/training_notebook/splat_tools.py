"""Helpers for the Parallel splat-training notebook (embedded into it by build.py).

Depends only on numpy and scipy so that it runs in Colab or Kaggle unchanged, and is tested here on
synthetic data. Nothing in this module imports Parallel.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

REQUIRED = ("x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
            "rot_0", "rot_1", "rot_2", "rot_3")
_TYPES = {"float": "<f4", "float32": "<f4", "double": "<f8", "float64": "<f8", "uchar": "u1", "uint8": "u1",
          "int": "<i4", "int32": "<i4", "uint": "<u4", "uint32": "<u4"}
_NAMES = {"<f4": "float", "<f8": "double", "|u1": "uchar", "<i4": "int", "<u4": "uint"}


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def verify_package(root) -> dict:
    """Check every file of an unzipped training package against its manifest. Returns the manifest."""
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("format") != "parallel-training-package/1":
        raise ValueError(f"unknown package format {manifest.get('format')!r}")
    bad = [f["path"] for f in manifest["files"] if not (root / f["path"]).exists()
           or sha256_file(root / f["path"]) != f["sha256"]]
    if bad:
        raise ValueError(f"{len(bad)} package files are missing or changed (for example {bad[0]}); "
                         "upload the package again")
    manifest["manifest_sha256"] = sha256_file(root / "manifest.json")
    return manifest


def read_points3d(path) -> np.ndarray:
    rows = [ln.split()[1:4] for ln in Path(path).read_text().splitlines() if ln and not ln.startswith("#")]
    return np.array(rows, dtype=float).reshape(-1, 3)


def read_ply(path) -> tuple[np.ndarray, list[str]]:
    """Binary little-endian PLY with one vertex element of scalar properties -> structured array, comments."""
    with open(path, "rb") as f:
        head = b""
        while b"end_header" not in head:
            chunk = f.read(4096)
            if not chunk:
                raise ValueError("PLY header is not terminated")
            head += chunk
        end = head.index(b"end_header")
        body_start = head.index(b"\n", end) + 1
        f.seek(body_start)
        lines = head[:end].decode("ascii").splitlines()
        props, count, comments = [], 0, []
        for ln in lines[1:]:
            p = ln.split()
            if not p:
                continue
            if p[0] == "comment":
                comments.append(ln[8:])
            elif p[0] == "format" and p[1] != "binary_little_endian":
                raise ValueError(f"only binary little-endian PLY is supported, got {p[1]}")
            elif p[0] == "element" and p[1] == "vertex":
                count = int(p[2])
            elif p[0] == "property":
                props.append((p[2], _TYPES[p[1]]))
        data = np.fromfile(f, dtype=np.dtype(props), count=count)
    if len(data) != count:
        raise ValueError(f"truncated PLY: expected {count} Gaussians, read {len(data)}")
    missing = [n for n in REQUIRED if n not in data.dtype.names]
    if missing:
        raise ValueError(f"not a Gaussian splat PLY: missing {missing}")
    return data, comments


def write_ply(path, data: np.ndarray, comments: list[str] | None = None) -> None:
    header = ["ply", "format binary_little_endian 1.0"] + [f"comment {c}" for c in comments or []]
    header.append(f"element vertex {len(data)}")
    header += [f"property {_NAMES[data.dtype[n].str]} {n}" for n in data.dtype.names]
    header.append("end_header")
    with open(path, "wb") as f:
        f.write(("\n".join(header) + "\n").encode("ascii"))
        data.tofile(f)


def positions(data: np.ndarray) -> np.ndarray:
    return np.stack([data["x"], data["y"], data["z"]], axis=1).astype(np.float64)


def drop_non_finite(data: np.ndarray) -> tuple[np.ndarray, int]:
    ok = np.ones(len(data), dtype=bool)
    for n in REQUIRED:
        ok &= np.isfinite(data[n])
    return data[ok], int((~ok).sum())


def drop_view_dependent_colour(data: np.ndarray) -> np.ndarray:
    """Remove f_rest_* (view-dependent colour); about five times smaller, same base colour."""
    keep = [n for n in data.dtype.names if not n.startswith("f_rest_")]
    out = np.zeros(len(data), dtype=[(n, data.dtype[n]) for n in keep])
    for n in keep:
        out[n] = data[n]
    return out


def load_dataparser_transform(path) -> tuple[np.ndarray, np.ndarray, float] | None:
    """nerfstudio's dataparser_transforms.json -> (R, t, scale) with x_train = scale * (R x + t)."""
    p = Path(path)
    if not p.exists():
        return None
    j = json.loads(p.read_text())
    m = np.array(j["transform"], dtype=float)
    return m[:3, :3], m[:3, 3], float(j.get("scale", 1.0))


def is_identity(R: np.ndarray, t: np.ndarray, s: float, tol: float = 1e-6) -> bool:
    return np.allclose(R, np.eye(3), atol=tol) and np.allclose(t, 0, atol=tol) and abs(s - 1.0) < tol


def to_capture_frame(data: np.ndarray, R: np.ndarray, t: np.ndarray, s: float) -> np.ndarray:
    """Undo nerfstudio's orientation, centring and scaling so the splat is in COLMAP's frame again.

    Positions: x = R^T (x_train / s - t). Gaussian sizes scale with the positions (log-scales shift by
    -log s); orientations compose with R^T. View-dependent colour is not rotated, so it is dropped.
    """
    from scipy.spatial.transform import Rotation

    out = drop_view_dependent_colour(data) if any(n.startswith("f_rest_") for n in data.dtype.names) else data.copy()
    xyz = (positions(out) / s - t) @ R  # rows: R^T v == v @ R
    out["x"], out["y"], out["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    for i in range(3):
        out[f"scale_{i}"] = out[f"scale_{i}"] - np.log(s)
    q = np.stack([out[f"rot_{i}"] for i in range(4)], axis=1).astype(np.float64)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    new = (Rotation.from_matrix(R.T) * Rotation.from_quat(q[:, [1, 2, 3, 0]])).as_quat()  # xyzw
    for i, col in enumerate((3, 0, 1, 2)):
        out[f"rot_{i}"] = new[:, col]
    return out


def alignment_check(splat_xyz: np.ndarray, sfm_xyz: np.ndarray, max_relative_distance: float = 0.05,
                    spread_ratio_range: tuple[float, float] = (0.5, 2.0), sample: int = 20_000) -> dict:
    """The same statistics Parallel applies on import (see parallel_scene.splat.alignment_check)."""
    from scipy.spatial import cKDTree

    rng = np.random.default_rng(0)
    sfm = np.asarray(sfm_xyz, dtype=np.float64)
    splat = np.asarray(splat_xyz, dtype=np.float64)
    if len(splat) > 500_000:
        splat = splat[rng.choice(len(splat), 500_000, replace=False)]
    if len(sfm) > sample:
        sfm = sfm[rng.choice(len(sfm), sample, replace=False)]
    sfm_spread = float(np.median(np.linalg.norm(sfm - np.median(sfm, axis=0), axis=1)))
    splat_spread = float(np.median(np.linalg.norm(splat - np.median(splat, axis=0), axis=1)))
    dist, _ = cKDTree(splat).query(sfm)
    median_rel = float(np.median(dist)) / sfm_spread
    ratio = splat_spread / sfm_spread
    ok = median_rel <= max_relative_distance and spread_ratio_range[0] <= ratio <= spread_ratio_range[1]
    return {"passed": bool(ok), "median_distance_fraction_of_spread": round(median_rel, 5),
            "spread_ratio_splat_to_sparse": round(ratio, 4)}
