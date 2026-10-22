"""COLMAP (structure from motion) wrapper and sparse-model parsing."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from parallel_scene.transforms import quat_to_matrix

EXHAUSTIVE_LIMIT = 120  # beyond this many frames use sequential matching


class ColmapError(RuntimeError):
    pass


def colmap_bin() -> str | None:
    return shutil.which("colmap")


def colmap_version() -> str | None:
    exe = colmap_bin()
    if not exe:
        return None
    out = subprocess.run([exe, "-h"], capture_output=True, text=True).stdout
    m = re.search(r"COLMAP (\S+)", out)
    return m.group(1) if m else None


@dataclass
class SparseImage:
    image_id: int
    name: str
    q_wxyz: np.ndarray  # world-to-camera rotation
    t: np.ndarray  # world-to-camera translation
    camera_id: int
    n_points: int

    @property
    def centre(self) -> np.ndarray:
        r = quat_to_matrix(self.q_wxyz)
        return -r.T @ self.t


@dataclass
class SparseModel:
    cameras: dict[int, dict]
    images: list[SparseImage]
    xyz: np.ndarray
    rgb: np.ndarray
    errors: np.ndarray
    track_lengths: np.ndarray

    @property
    def mean_reprojection_error(self) -> float:
        return float(self.errors.mean()) if len(self.errors) else float("nan")

    @property
    def mean_track_length(self) -> float:
        return float(self.track_lengths.mean()) if len(self.track_lengths) else 0.0


def run(args: list[str], log: Path, timeout: float, cancel=None) -> None:
    exe = colmap_bin()
    if not exe:
        raise ColmapError("COLMAP is not installed on this worker")
    with open(log, "a") as fh:
        fh.write("$ colmap " + " ".join(args) + "\n")
        fh.flush()
        proc = subprocess.Popen([exe, *args], stdout=fh, stderr=subprocess.STDOUT)
        try:
            import time

            t0 = time.monotonic()
            while proc.poll() is None:
                if cancel and cancel():
                    proc.kill()
                    raise ColmapError("cancelled")
                if time.monotonic() - t0 > timeout:
                    proc.kill()
                    raise ColmapError(f"COLMAP {args[0]} exceeded {timeout:.0f} s")
                time.sleep(0.5)
        finally:
            if proc.poll() is None:
                proc.kill()
    if proc.returncode != 0:
        raise ColmapError(f"COLMAP {args[0]} failed (exit {proc.returncode}); see {log.name}")


def reconstruct(images: Path, work: Path, log: Path, n_images: int, threads: int = 4, timeout: float = 3600,
                cancel=None, camera_model: str = "SIMPLE_RADIAL") -> Path:
    """Feature extraction, matching and incremental mapping. Returns the TXT model directory of the largest model."""
    work.mkdir(parents=True, exist_ok=True)
    db = work / "database.db"
    db.unlink(missing_ok=True)
    sparse = work / "sparse"
    shutil.rmtree(sparse, ignore_errors=True)
    sparse.mkdir()
    run(["feature_extractor", "--database_path", str(db), "--image_path", str(images),
         "--ImageReader.single_camera", "1", "--ImageReader.camera_model", camera_model,
         "--SiftExtraction.use_gpu", "0", "--SiftExtraction.max_num_features", "4096",
         "--SiftExtraction.num_threads", str(threads)], log, timeout, cancel)
    if n_images <= EXHAUSTIVE_LIMIT:
        run(["exhaustive_matcher", "--database_path", str(db), "--SiftMatching.use_gpu", "0",
             "--SiftMatching.num_threads", str(threads)], log, timeout, cancel)
    else:
        run(["sequential_matcher", "--database_path", str(db), "--SiftMatching.use_gpu", "0",
             "--SiftMatching.num_threads", str(threads), "--SequentialMatching.overlap", "15",
             "--SequentialMatching.quadratic_overlap", "1"], log, timeout, cancel)
    run(["mapper", "--database_path", str(db), "--image_path", str(images), "--output_path", str(sparse),
         "--Mapper.num_threads", str(threads)], log, timeout, cancel)
    models = [p for p in sparse.iterdir() if p.is_dir()]
    if not models:
        raise ColmapError("COLMAP produced no sparse model (insufficient overlap or features)")
    best = max(models, key=lambda p: (p / "images.bin").stat().st_size)
    txt = work / "sparse_txt"
    shutil.rmtree(txt, ignore_errors=True)
    txt.mkdir()
    run(["model_converter", "--input_path", str(best), "--output_path", str(txt), "--output_type", "TXT"], log, timeout)
    return txt


def parse_txt(model: Path) -> SparseModel:
    cameras = {}
    for ln in (model / "cameras.txt").read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        p = ln.split()
        cameras[int(p[0])] = {"model": p[1], "width": int(p[2]), "height": int(p[3]), "params": [float(x) for x in p[4:]]}
    images = []
    lines = [ln for ln in (model / "images.txt").read_text().splitlines() if not ln.startswith("#")]
    for i in range(0, len(lines) - 1, 2):
        p = lines[i].split()
        if len(p) < 10:
            continue
        pts = lines[i + 1].split()
        n_obs = sum(1 for j in range(2, len(pts), 3) if pts[j] != "-1")
        images.append(SparseImage(int(p[0]), p[9], np.array([float(x) for x in p[1:5]]),
                                  np.array([float(x) for x in p[5:8]]), int(p[8]), n_obs))
    xyz, rgb, err, track = [], [], [], []
    for ln in (model / "points3D.txt").read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        p = ln.split()
        xyz.append([float(p[1]), float(p[2]), float(p[3])])
        rgb.append([int(p[4]), int(p[5]), int(p[6])])
        err.append(float(p[7]))
        track.append((len(p) - 8) // 2)
    return SparseModel(cameras, sorted(images, key=lambda im: im.name), np.array(xyz).reshape(-1, 3),
                       np.array(rgb, dtype=np.uint8).reshape(-1, 3), np.array(err), np.array(track))


def ransac_plane(points: np.ndarray, threshold: float, iterations: int = 500, seed: int = 0) -> tuple[np.ndarray, float, float]:
    """Dominant plane n.x + d = 0 by RANSAC; returns (unit normal, d, inlier fraction)."""
    rng = np.random.default_rng(seed)
    best = (np.array([0.0, 0.0, 1.0]), 0.0, 0.0)
    if len(points) < 3:
        return best
    for _ in range(iterations):
        a, b, c = points[rng.choice(len(points), 3, replace=False)]
        n = np.cross(b - a, c - a)
        norm = np.linalg.norm(n)
        if norm < 1e-12:
            continue
        n /= norm
        d = -n @ a
        frac = float(np.mean(np.abs(points @ n + d) < threshold))
        if frac > best[2]:
            best = (n, float(d), frac)
    return best
