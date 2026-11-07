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


def run_streaming(cmd: list[str], tail: int = 80) -> None:
    """Run a command, printing its output line by line as it arrives.

    In Colab a child process's output does not appear in the cell, so a failure would show only an exit code.
    On failure this raises with the last lines of output, which is where nerfstudio reports the cause.
    """
    import collections
    import subprocess

    last: collections.deque[str] = collections.deque(maxlen=tail)
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            last.append(line)
    if proc.returncode:
        raise RuntimeError(f"{cmd[0]} stopped with exit code {proc.returncode}. Its last lines were:\n" + "".join(last))


OPEN3D_PLACEHOLDER = '''"""Placeholder written by the Parallel training notebook: open3d has no build for this Python version.

nerfstudio imports open3d in its exporter even though Gaussian splat export never uses it. Any real use raises.
"""


class _Missing:
    def __init__(self, name):
        self._name = name

    def __getattr__(self, attr):
        return _Missing(self._name + "." + attr)

    def __call__(self, *args, **kwargs):
        raise RuntimeError("open3d is not installed: " + self._name + " cannot be used")


def __getattr__(attr):
    return _Missing("open3d." + attr)
'''


def ensure_open3d_placeholder(site_dir=None) -> bool:
    """Install the placeholder when open3d is missing (it has no Python 3.13 build). Returns True if written."""
    import importlib.util
    import site

    if site_dir is None and importlib.util.find_spec("open3d") is not None:
        return False
    target = Path(site_dir or site.getsitepackages()[0]) / "open3d"
    target.mkdir(parents=True, exist_ok=True)
    (target / "__init__.py").write_text(OPEN3D_PLACEHOLDER)
    return True


def make_downscaled_images(root, factor: int) -> int:
    """Write images_<factor>/ next to images/, each frame shrunk by an integer factor (floor rounding).

    nerfstudio's COLMAP data parser reads downscaled frames from that folder. When it is missing it stops to ask
    a yes/no question on the terminal, which nobody can answer inside a notebook, so the training exits.
    Floor rounding matches the camera sizes nerfstudio computes for the same factor. Returns the frames written.
    """
    from PIL import Image

    root = Path(root)
    if factor <= 1:
        return 0
    src, dst = root / "images", root / f"images_{factor}"
    dst.mkdir(exist_ok=True)
    written = 0
    for f in sorted(p for p in src.iterdir() if p.is_file()):
        out = dst / f.name
        if out.exists():
            continue
        with Image.open(f) as im:
            w, h = im.size
            im.resize((max(1, w // factor), max(1, h // factor)), Image.LANCZOS).save(out)
        written += 1
    return written


CODE_HEADER = "X-Parallel-Training-Code"


def parse_access_code(code: str) -> tuple[str, str]:
    """Split an access code from Parallel ("https://your-parallel-site#ptk_...") into the site and the token."""
    code = (code or "").strip()
    site, sep, token = code.rpartition("#")
    if not sep or not token.startswith("ptk_") or not site.startswith(("https://", "http://")):
        raise ValueError("That is not a Parallel access code. Copy the whole code from the reconstruction job "
                         "(it starts with your Parallel address and contains '#ptk_').")
    return site.rstrip("/"), token


def _refusal(resp) -> str:
    try:
        err = resp.json().get("error", {})
        return f"{err.get('code', resp.status_code)}: {err.get('message', resp.text[:300])}"
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:300]}"


def fetch_package(code: str, dest) -> Path:
    """Download the job's training package from Parallel using the access code. Returns the zip's path."""
    import requests

    site, token = parse_access_code(code)
    dest = Path(dest)
    with requests.get(f"{site}/api/v1/training-access/package", headers={CODE_HEADER: token}, stream=True,
                      timeout=(30, 600)) as r:
        if r.status_code != 200:
            raise RuntimeError("Parallel refused the download. " + _refusal(r))
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return dest


def send_splat(code: str, splat_path, info_path) -> dict:
    """Import the trained splat into the job in Parallel using the access code. Returns Parallel's answer."""
    import requests

    site, token = parse_access_code(code)
    with open(splat_path, "rb") as s, open(info_path, "rb") as i:
        r = requests.post(f"{site}/api/v1/training-access/import-splat", headers={CODE_HEADER: token},
                          files={"splat": ("splat.ply", s, "application/octet-stream"),
                                 "training_info": ("training_info.json", i, "application/json")},
                          timeout=(30, 1800))
    if r.status_code != 201:
        raise RuntimeError("Parallel did not import the splat. " + _refusal(r))
    return r.json()


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
                    min_spread_ratio: float = 0.5, sample: int = 20_000) -> dict:
    """The same statistics Parallel applies on import (see parallel_scene.splat.alignment_check).

    A splat is usually wider than the sparse points (it reaches background they missed), so only a splat much
    smaller than them is refused on size; a moved or re-scaled one fails the distance test.
    """
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
    ok = median_rel <= max_relative_distance and ratio >= min_spread_ratio
    return {"passed": bool(ok), "median_distance_fraction_of_spread": round(median_rel, 5),
            "spread_ratio_splat_to_sparse": round(ratio, 4)}
