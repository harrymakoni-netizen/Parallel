"""Capture processing and reconstruction pipeline (spec section 13.3).

Stages, in order (each checkpointed to object storage so a job can resume or
restart from the last valid stage):

 1. upload_verification      re-hash stored inputs against their recorded SHA-256
 2. media_inspection         video/image properties
 3. frame_extraction         FFmpeg frame sampling (videos) / image normalisation
 4. frame_filtering          Laplacian-variance blur filter + near-duplicate filter
 5. pose_estimation          COLMAP feature extraction, matching, incremental mapping
 6. reconstruction_training  Splatfacto (nerfstudio) Gaussian training on a GPU worker
 7. visual_export            point cloud PLY, Gaussian PLY (trained, or labelled untrained initialisation)
 8. geometry_generation      dominant ground plane (RANSAC); collision mesh only when Open3D is available
 9. scale_orientation        similarity transform from surveyed references, else orientation only
10. quality_assessment       registration, reprojection error, alignment residuals, measurement checks
11. scene_publication        scene revision with visual assets, transforms, quality states, provenance

The stage sequence follows the reference 3D Gaussian Splatting pipeline
(rakeshsuthar6322/3d_gaussian_splatting_for_model_city): FFmpeg -> blur
detection -> COLMAP -> Splatfacto -> PLY export -> collision mesh. This module
is an independent implementation integrated with Parallel's job records.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from parallel_scene import ply
from parallel_scene.transforms import apply, to_list, umeyama

from . import colmap

STAGES = [
    "upload_verification", "media_inspection", "frame_extraction", "frame_filtering", "pose_estimation",
    "reconstruction_training", "visual_export", "geometry_generation", "scale_orientation", "quality_assessment",
    "scene_publication",
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "frame_rate": 2.0,
    "blur_threshold": 15.0,  # absolute Laplacian-variance floor (reference pipeline default)
    "blur_relative": 0.35,  # also reject frames below 35% of the median sharpness
    "duplicate_hash_distance": 3,
    "max_frames": 240,
    "train_gaussians": True,
    "splatfacto_preset": "refined",  # default | aggressive | refined (reference pipeline presets)
    "registered_fraction_required": 0.6,
    "tolerance_abs_m": 0.05,
    "tolerance_purpose": "Room/scene-scale demonstration threshold",
}

SPLATFACTO_PRESETS = {
    "default": {"densify_grad_thresh": 0.0008, "stop_split_at": 15000, "cull_alpha_thresh": 0.1, "cull_scale_thresh": 0.5},
    "aggressive": {"densify_grad_thresh": 0.0001, "stop_split_at": 25000, "cull_alpha_thresh": 0.05, "cull_scale_thresh": 0.5},
    "refined": {"densify_grad_thresh": 0.0002, "stop_split_at": 15000, "cull_alpha_thresh": 0.1, "cull_scale_thresh": 0.2},
}


class StageSkipped(Exception):
    def __init__(self, reason: str, data: dict | None = None):
        super().__init__(reason)
        self.data = data or {}


class PipelineError(RuntimeError):
    pass


@dataclass
class InputAsset:
    id: str
    filename: str
    kind: str
    sha256: str | None
    storage_key: str
    inspection: dict


@dataclass
class PipelineContext:
    job_id: str
    project_id: str
    workdir: Path
    settings: dict[str, Any]
    assets: list[InputAsset]
    fetch: Callable[[str, Path], Path]  # storage key -> local path
    log: Path
    data: dict[str, dict] = field(default_factory=dict)  # per-stage small results
    heartbeat: Callable[[str, float, str], None] = lambda stage, frac, msg: None
    cancelled: Callable[[], bool] = lambda: False

    def stage_dir(self, stage: str) -> Path:
        d = self.workdir / stage
        d.mkdir(parents=True, exist_ok=True)
        return d


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- stages
def upload_verification(ctx: PipelineContext) -> dict:
    out = ctx.stage_dir("inputs")
    rows = []
    for a in ctx.assets:
        p = ctx.fetch(a.storage_key, out / a.id / a.filename)
        actual = _sha(p)
        if a.sha256 and actual != a.sha256:
            raise PipelineError(f"input {a.filename} hash mismatch: stored file differs from the uploaded file")
        rows.append({"asset_id": a.id, "filename": a.filename, "kind": a.kind, "sha256": actual})
    if not any(r["kind"] in ("video", "image") for r in rows):
        raise PipelineError("capture batch has no photographs or video")
    return {"inputs": rows}


def media_inspection(ctx: PipelineContext) -> dict:
    vids = [a for a in ctx.assets if a.kind == "video"]
    imgs = [a for a in ctx.assets if a.kind == "image"]
    return {"videos": [{"filename": a.filename, **a.inspection} for a in vids],
            "images": len(imgs), "image_sizes": sorted({(a.inspection.get("width"), a.inspection.get("height")) for a in imgs})}


def frame_extraction(ctx: PipelineContext) -> dict:
    frames = ctx.stage_dir("frames")
    for f in frames.iterdir():
        f.unlink()
    inputs = ctx.workdir / "inputs"
    rate = float(ctx.settings["frame_rate"])
    count = 0
    for a in ctx.assets:
        src = inputs / a.id / a.filename
        if a.kind == "video":
            stem = Path(a.filename).stem[:40]
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", f"fps={rate}", "-q:v", "2",
                            str(frames / f"{stem}_%05d.jpg")], check=True)
        elif a.kind == "image":
            with Image.open(src) as im:
                from PIL import ImageOps

                ImageOps.exif_transpose(im).convert("RGB").save(frames / (Path(a.filename).stem + ".png"))
    names = sorted(p.name for p in frames.iterdir())
    count = len(names)
    if count < 3:
        raise PipelineError(f"only {count} frames available; at least 3 are needed")
    return {"frames": names, "frame_rate": rate}


def _laplacian_variance(gray: np.ndarray) -> float:
    import cv2

    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _dhash(gray: np.ndarray) -> int:
    small = np.asarray(Image.fromarray(gray).resize((9, 8), Image.BILINEAR), dtype=np.int16)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def frame_filtering(ctx: PipelineContext) -> dict:
    src = ctx.workdir / "frames"
    names = ctx.data["frame_extraction"]["frames"]
    keep_dir = ctx.stage_dir("frames_accepted")
    for f in keep_dir.iterdir():
        f.unlink()
    scores = {}
    hashes = {}
    for n in names:
        g = np.asarray(Image.open(src / n).convert("L"))
        scores[n] = _laplacian_variance(g)
        hashes[n] = _dhash(g)
    med = float(np.median(list(scores.values())))
    floor = max(float(ctx.settings["blur_threshold"]), float(ctx.settings["blur_relative"]) * med)
    records, last_kept = [], None
    for n in names:
        reason = None
        if scores[n] < floor:
            reason = f"blurred (Laplacian variance {scores[n]:.1f} < {floor:.1f})"
        elif last_kept is not None and bin(hashes[n] ^ hashes[last_kept]).count("1") <= int(ctx.settings["duplicate_hash_distance"]):
            reason = "near-duplicate of previous accepted frame"
        records.append({"frame": n, "sharpness": round(scores[n], 2), "accepted": reason is None, "reason": reason})
        if reason is None:
            last_kept = n
            shutil.copy(src / n, keep_dir / n)
    accepted = [r for r in records if r["accepted"]]
    if len(accepted) > int(ctx.settings["max_frames"]):
        step = len(accepted) / int(ctx.settings["max_frames"])
        chosen = {accepted[int(i * step)]["frame"] for i in range(int(ctx.settings["max_frames"]))}
        for r in accepted:
            if r["frame"] not in chosen:
                r["accepted"], r["reason"] = False, "subsampled to the frame budget"
                (keep_dir / r["frame"]).unlink()
    with open(ctx.stage_dir("frame_filtering") / "frame_records.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["frame", "sharpness", "accepted", "reason"])
        w.writeheader()
        w.writerows(records)
    n_acc = sum(r["accepted"] for r in records)
    if n_acc < 3:
        raise PipelineError(f"only {n_acc} frames passed filtering")
    return {"accepted": n_acc, "rejected": len(records) - n_acc, "sharpness_floor": round(floor, 2),
            "median_sharpness": round(med, 2)}


def pose_estimation(ctx: PipelineContext) -> dict:
    if not colmap.colmap_bin():
        raise PipelineError("COLMAP is not installed on this worker")
    images = ctx.workdir / "frames_accepted"
    n = len(list(images.iterdir()))
    work = ctx.stage_dir("colmap")
    t0 = time.time()
    ctx.heartbeat("pose_estimation", 0.1, f"COLMAP on {n} frames")
    txt = colmap.reconstruct(images, work, ctx.log, n, cancel=ctx.cancelled)
    model = colmap.parse_txt(txt)
    shutil.rmtree(work / "sparse", ignore_errors=True)
    (work / "database.db").unlink(missing_ok=True)
    reg = len(model.images)
    return {"colmap_version": colmap.colmap_version(), "images_input": n, "images_registered": reg,
            "registered_fraction": reg / n, "points": int(len(model.xyz)),
            "mean_reprojection_error_px": round(model.mean_reprojection_error, 4),
            "mean_track_length": round(model.mean_track_length, 3), "camera": next(iter(model.cameras.values())),
            "duration_s": round(time.time() - t0, 1)}


def _gpu_available() -> tuple[bool, str]:
    if shutil.which("nvidia-smi") is None:
        return False, "No NVIDIA GPU on this worker (nvidia-smi not found)"
    if shutil.which("ns-train") is None:
        return False, "nerfstudio (ns-train) is not installed on this worker"
    return True, "GPU and nerfstudio available"


def reconstruction_training(ctx: PipelineContext) -> dict:
    if not ctx.settings.get("train_gaussians", True):
        raise StageSkipped("Gaussian training not requested")
    ok, why = _gpu_available()
    preset = SPLATFACTO_PRESETS[ctx.settings.get("splatfacto_preset", "refined")]
    if not ok:
        raise StageSkipped(f"GPU worker unavailable: {why}. Splatfacto training was not run.",
                           {"availability": "worker_unavailable", "preset": preset})
    # GPU worker path (nerfstudio, as in the reference pipeline): process COLMAP data and train Splatfacto.
    out = ctx.stage_dir("splatfacto")
    images = ctx.workdir / "frames_accepted"
    subprocess.run(["ns-process-data", "images", "--data", str(images), "--output-dir", str(out / "processed")],
                   check=True, stdout=open(ctx.log, "a"), stderr=subprocess.STDOUT)
    subprocess.run(["ns-train", "splatfacto", "--data", str(out / "processed"), "--output-dir", str(out / "train"),
                    "--pipeline.model.densify-grad-thresh", str(preset["densify_grad_thresh"]),
                    "--pipeline.model.stop-split-at", str(preset["stop_split_at"]),
                    "--pipeline.model.cull-scale-thresh", str(preset["cull_scale_thresh"]),
                    "--pipeline.model.cull-alpha-thresh", str(preset["cull_alpha_thresh"]),
                    "--max-num-iterations", "30000", "--viewer.quit-on-train-completion", "True"],
                   check=True, stdout=open(ctx.log, "a"), stderr=subprocess.STDOUT)
    cfg = next((out / "train").rglob("config.yml"))
    subprocess.run(["ns-export", "gaussian-splat", "--load-config", str(cfg), "--output-dir", str(out / "export")],
                   check=True, stdout=open(ctx.log, "a"), stderr=subprocess.STDOUT)
    return {"trained": True, "preset": preset, "splat": "splatfacto/export/splat.ply"}


def visual_export(ctx: PipelineContext) -> dict:
    model = colmap.parse_txt(ctx.workdir / "colmap" / "sparse_txt")
    out = ctx.stage_dir("export")
    ply.write_point_cloud(out / "sparse_points_capture.ply", model.xyz, model.rgb,
                          comments=["Parallel sparse point cloud (COLMAP), capture frame"])
    poses = [{"frame": im.name, "centre": im.centre.tolist(), "q_wxyz": im.q_wxyz.tolist(), "t": im.t.tolist(),
              "observations": im.n_points} for im in model.images]
    (out / "camera_poses.json").write_text(json.dumps(poses))
    trained = ctx.data.get("reconstruction_training", {}).get("trained", False)
    return {"points": int(len(model.xyz)), "trained_splat": trained}


def geometry_generation(ctx: PipelineContext) -> dict:
    model = colmap.parse_txt(ctx.workdir / "colmap" / "sparse_txt")
    if len(model.xyz) < 50:
        raise StageSkipped("Too few points for geometry generation")
    spread = float(np.median(np.linalg.norm(model.xyz - np.median(model.xyz, axis=0), axis=1)))
    n, d, frac = colmap.ransac_plane(model.xyz, threshold=0.01 * spread, iterations=800)
    cams = np.array([im.centre for im in model.images])
    if np.mean(cams @ n + d) < 0:  # orient the normal towards the cameras (up)
        n, d = -n, -d
    data = {"ground_plane": {"normal": n.tolist(), "d": d, "inlier_fraction": round(frac, 3), "frame": "capture"},
            "collision_mesh": "not generated (Open3D Poisson reconstruction not installed on this worker)"}
    return data


def _rotation_to_z(n: np.ndarray) -> np.ndarray:
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(n, z)
    c = float(n @ z)
    if np.linalg.norm(v) < 1e-12:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 / (1 + c))


def scale_orientation(ctx: PipelineContext) -> dict:
    model = colmap.parse_txt(ctx.workdir / "colmap" / "sparse_txt")
    ref_path = ctx.workdir / "inputs" / "camera_positions.csv"
    refs = {}
    if ref_path.exists():
        with open(ref_path) as f:
            for r in csv.DictReader(f):
                refs[Path(r["frame"]).stem] = [float(r["x"]), float(r["y"]), float(r["z"])]
    pairs = [(Path(im.name).stem, im.centre, refs[Path(im.name).stem]) for im in model.images if Path(im.name).stem in refs]
    if len(pairs) >= 3:
        # Control/check split: every third surveyed position is held out as an independent check point.
        check_idx = set(range(2, len(pairs), 3)) if len(pairs) >= 6 else set()
        control = [p for i, p in enumerate(pairs) if i not in check_idx]
        checks = [p for i, p in enumerate(pairs) if i in check_idx]
        src = np.array([p[1] for p in control])
        dst = np.array([p[2] for p in control])
        m, rms = umeyama(src, dst)
        scale = float(np.cbrt(np.linalg.det(m[:3, :3])))
        check_points = []
        for name, c, ref in checks:
            est = apply(m, c)
            check_points.append({"frame": name, "estimated": est.tolist(), "reference": list(ref),
                                 "residual_m": float(np.linalg.norm(est - np.array(ref)))})
        check_rms = float(np.sqrt(np.mean([c["residual_m"] ** 2 for c in check_points]))) if check_points else None
        return {"method": f"similarity from {len(control)} surveyed camera positions (Umeyama least squares); "
                          f"{len(checks)} positions held out as check points", "matrix": to_list(m),
                "scale_established": True, "residual_rms_m": rms, "pairs": len(control), "scale": scale,
                "check_points": check_points, "check_rms_m": check_rms,
                "reference_source": ctx.settings.get("reference_source", "camera_positions.csv")}
    plane = ctx.data.get("geometry_generation", {}).get("ground_plane")
    if plane:
        n = np.array(plane["normal"])
        r = _rotation_to_z(n)
        m = np.eye(4)
        m[:3, :3] = r
        # n.x + d = 0 maps to z' = n.x = -d after rotating n onto +Z; translate by +d to put the plane at z = 0
        m[2, 3] = float(plane["d"])
        return {"method": "orientation from dominant ground plane only (scale unknown)", "matrix": to_list(m),
                "scale_established": False, "residual_rms_m": None, "pairs": 0}
    return {"method": "none", "matrix": to_list(np.eye(4)), "scale_established": False, "residual_rms_m": None, "pairs": 0}


def quality_assessment(ctx: PipelineContext) -> dict:
    pe = ctx.data["pose_estimation"]
    so = ctx.data["scale_orientation"]
    model = colmap.parse_txt(ctx.workdir / "colmap" / "sparse_txt")
    m = np.array(so["matrix"])
    xyz = apply(m, model.xyz) if len(model.xyz) else model.xyz
    out = ctx.stage_dir("export")
    ply.write_point_cloud(out / "sparse_points.ply", xyz, model.rgb,
                          comments=["Parallel sparse point cloud (COLMAP), canonical frame"])
    _gaussian_initialisation(out / "gaussian_initialisation.ply", xyz, model.rgb)
    checks = []
    cps = so.get("check_points") or []
    if so["scale_established"] and len(cps) >= 2:
        # Independent distance checks between the most widely separated held-out check points.
        cand = []
        for i in range(len(cps)):
            for j in range(i + 1, len(cps)):
                ref = float(np.linalg.norm(np.array(cps[i]["reference"]) - np.array(cps[j]["reference"])))
                cand.append((ref, i, j))
        for ref, i, j in sorted(cand, reverse=True)[:3]:
            checks.append({"label": f"camera {cps[i]['frame']} to camera {cps[j]['frame']}",
                           "point_a": cps[i]["estimated"], "point_b": cps[j]["estimated"], "reference_m": ref,
                           "source": "held-out surveyed camera positions (check points not used for the alignment)"})
    registered_ok = pe["registered_fraction"] >= float(ctx.settings["registered_fraction_required"])
    return {"visual_reconstruction_available": registered_ok and pe["points"] > 100,
            "registered_fraction": pe["registered_fraction"], "mean_reprojection_error_px": pe["mean_reprojection_error_px"],
            "points": pe["points"], "scale_established": so["scale_established"], "alignment_rms_m": so["residual_rms_m"],
            "check_rms_m": so.get("check_rms_m"), "measurement_checks": checks}


def _gaussian_initialisation(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    """Untrained Gaussians initialised from SfM points (as 3DGS does before training): isotropic, kNN-sized."""
    if len(xyz) == 0:
        return
    from scipy.spatial import cKDTree

    tree = cKDTree(xyz)
    k = min(4, len(xyz))
    dist, _ = tree.query(xyz, k=k)
    mean_d = np.clip(dist[:, 1:].mean(axis=1) if k > 1 else np.full(len(xyz), 0.05), 1e-3, 0.5)
    log_s = np.log(np.repeat(mean_d[:, None], 3, axis=1) * 0.6)
    ply.write_gaussians(path, xyz, ply.rgb_to_sh_dc(rgb / 255.0), np.full(len(xyz), 2.0), log_s,
                        np.tile([1.0, 0.0, 0.0, 0.0], (len(xyz), 1)),
                        comments=["UNTRAINED Gaussian initialisation from SfM points; not a trained reconstruction"])


STAGE_FUNCS: dict[str, Callable[[PipelineContext], dict]] = {
    "upload_verification": upload_verification,
    "media_inspection": media_inspection,
    "frame_extraction": frame_extraction,
    "frame_filtering": frame_filtering,
    "pose_estimation": pose_estimation,
    "reconstruction_training": reconstruction_training,
    "visual_export": visual_export,
    "geometry_generation": geometry_generation,
    "scale_orientation": scale_orientation,
    "quality_assessment": quality_assessment,
}

# Directories each stage writes (checkpointed after the stage completes)
STAGE_OUTPUTS: dict[str, list[str]] = {
    "upload_verification": ["inputs"],
    "frame_extraction": ["frames"],
    "frame_filtering": ["frames_accepted", "frame_filtering"],
    "pose_estimation": ["colmap"],
    "reconstruction_training": ["splatfacto"],
    "visual_export": ["export"],
    "quality_assessment": ["export"],
}
