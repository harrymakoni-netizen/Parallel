"""Minimal, dependency-free PLY reading/writing for point clouds and Gaussian splats.

Supports ``ascii`` and ``binary_little_endian`` vertex elements with scalar
properties. Gaussian splat PLY files (3DGS / Splatfacto export) are detected by
their ``f_dc_*``, ``scale_*``, ``rot_*`` and ``opacity`` properties.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

_PLY_TYPES = {
    "char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2", "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4", "double": "f8", "float64": "f8",
}  # fmt: skip

GAUSSIAN_REQUIRED = {"x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"}  # fmt: skip
MAX_HEADER_BYTES = 64 * 1024


class PlyError(ValueError):
    pass


@dataclass
class PlyHeader:
    format: str
    vertex_count: int
    properties: list[tuple[str, str]]
    header_bytes: int
    other_elements: list[tuple[str, int]]

    @property
    def is_gaussian_splat(self) -> bool:
        return {p for p, _ in self.properties} >= GAUSSIAN_REQUIRED

    @property
    def has_colour(self) -> bool:
        names = {p for p, _ in self.properties}
        return {"red", "green", "blue"} <= names or {"f_dc_0", "f_dc_1", "f_dc_2"} <= names


def read_header(path: str | Path) -> PlyHeader:
    with open(path, "rb") as f:
        head = f.read(MAX_HEADER_BYTES)
    if not head.startswith(b"ply"):
        raise PlyError("not a PLY file (missing 'ply' magic)")
    end = head.find(b"end_header")
    if end < 0:
        raise PlyError("PLY header not terminated within 64 KiB")
    nl = head.find(b"\n", end)
    header_bytes = nl + 1
    lines = head[:end].decode("ascii", errors="strict").splitlines()
    fmt = None
    vertex_count = -1
    props: list[tuple[str, str]] = []
    others: list[tuple[str, int]] = []
    current = None
    for line in lines[1:]:
        parts = line.split()
        if not parts or parts[0] in ("comment", "obj_info"):
            continue
        if parts[0] == "format":
            fmt = parts[1]
        elif parts[0] == "element":
            current = parts[1]
            if current == "vertex":
                vertex_count = int(parts[2])
            else:
                others.append((current, int(parts[2])))
        elif parts[0] == "property" and current == "vertex":
            if parts[1] == "list":
                raise PlyError("list properties on vertices are not supported")
            if parts[1] not in _PLY_TYPES:
                raise PlyError(f"unsupported PLY property type {parts[1]}")
            props.append((parts[2], _PLY_TYPES[parts[1]]))
    if fmt not in ("ascii", "binary_little_endian"):
        raise PlyError(f"unsupported PLY format {fmt}")
    if vertex_count < 0:
        raise PlyError("PLY has no vertex element")
    return PlyHeader(fmt, vertex_count, props, header_bytes, others)


def read_vertices(path: str | Path, max_vertices: int = 50_000_000) -> tuple[PlyHeader, np.ndarray]:
    h = read_header(path)
    if h.vertex_count > max_vertices:
        raise PlyError(f"PLY has {h.vertex_count} vertices, above the limit of {max_vertices}")
    dtype = np.dtype([(n, "<" + t) for n, t in h.properties])
    if h.format == "binary_little_endian":
        with open(path, "rb") as f:
            f.seek(h.header_bytes)
            data = np.fromfile(f, dtype=dtype, count=h.vertex_count)
        if len(data) != h.vertex_count:
            raise PlyError(f"truncated PLY: expected {h.vertex_count} vertices, read {len(data)}")
    else:
        with open(path, "rb") as f:
            f.seek(h.header_bytes)
            rows = np.loadtxt(f, max_rows=h.vertex_count, ndmin=2)
        data = np.zeros(h.vertex_count, dtype=dtype)
        for i, (n, _) in enumerate(h.properties):
            data[n] = rows[:, i]
    return h, data


def write_point_cloud(
    path: str | Path, xyz: np.ndarray, rgb: np.ndarray | None = None, comments: list[str] | None = None
) -> None:
    xyz = np.asarray(xyz, dtype=np.float32)
    fields = [("x", "<f4"), ("y", "<f4"), ("z", "<f4")]
    if rgb is not None:
        fields += [("red", "u1"), ("green", "u1"), ("blue", "u1")]
    arr = np.zeros(len(xyz), dtype=fields)
    arr["x"], arr["y"], arr["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    if rgb is not None:
        rgb = np.clip(np.asarray(rgb), 0, 255).astype(np.uint8)
        arr["red"], arr["green"], arr["blue"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    _write(path, arr, comments)


def write_gaussians(
    path: str | Path,
    xyz: np.ndarray,
    f_dc: np.ndarray,
    opacity_logit: np.ndarray,
    log_scales: np.ndarray,
    rot_wxyz: np.ndarray,
    comments: list[str] | None = None,
) -> None:
    """Write a 3DGS-compatible PLY (SH degree 0)."""
    n = len(xyz)
    names = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
             "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]  # fmt: skip
    arr = np.zeros(n, dtype=[(k, "<f4") for k in names])
    arr["x"], arr["y"], arr["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    for i in range(3):
        arr[f"f_dc_{i}"] = f_dc[:, i]
        arr[f"scale_{i}"] = log_scales[:, i]
    arr["opacity"] = opacity_logit
    for i in range(4):
        arr[f"rot_{i}"] = rot_wxyz[:, i]
    _write(path, arr, comments)


def _write(path: str | Path, arr: np.ndarray, comments: list[str] | None) -> None:
    type_names = {"<f4": "float", "|u1": "uchar", "<f8": "double", "<i4": "int"}
    header = ["ply", "format binary_little_endian 1.0"]
    header += [f"comment {c}" for c in comments or []]
    header.append(f"element vertex {len(arr)}")
    for name in arr.dtype.names:
        header.append(f"property {type_names[arr.dtype[name].str]} {name}")
    header.append("end_header")
    with open(path, "wb") as f:
        f.write(("\n".join(header) + "\n").encode("ascii"))
        arr.tofile(f)


SH_C0 = 0.28209479177387814


def rgb_to_sh_dc(rgb01: np.ndarray) -> np.ndarray:
    return (np.asarray(rgb01, dtype=float) - 0.5) / SH_C0


def sh_dc_to_rgb(f_dc: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(f_dc, dtype=float) * SH_C0 + 0.5, 0.0, 1.0)
