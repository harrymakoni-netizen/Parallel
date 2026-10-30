"""Training a Gaussian splat outside Parallel (a free or rented GPU) and bringing it back.

Parallel's own training needs a GPU worker. When there is none, a reconstruction can be exported as a
training package, trained elsewhere with the supplied notebook, and the resulting splat imported as a
new scene revision. Nothing here claims Parallel trained or verified the splat: it is recorded as an
imported visual with its source, who added it and the alignment check it passed, and it remains an
appearance layer (a visual reconstruction is never walkable or solid geometry).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response
from sqlalchemy import select

from parallel_contracts.provenance import Provenance, ProvenanceKind
from parallel_contracts.scene import SceneRevisionContent, VisualAsset, VisualAssetKind
from parallel_core.access import Action
from parallel_core.audit import record as audit
from parallel_core.db import new_id, utcnow
from parallel_core.models import ReconstructionJob, Scene, SceneRevision, SourceAsset
from parallel_core.revisions import create_scene_revision
from parallel_core.storage import get_storage
from parallel_scene import splat as splat_check

from ..deps import DB, CurrentUser
from ..errors import ApiError
from ..open_access import guard_storage, upload_limit
from ..schemas import AssetOut
from .assets import received_file, store_upload
from .reconstructions import _job

router = APIRouter(tags=["external-training"])

PACKAGE_FORMAT = "parallel-training-package/1"
NOTEBOOK = Path(__file__).resolve().parent.parent / "resources" / "parallel_splat_training.ipynb"
MAX_INFO_BYTES = 64 * 1024
INFO_TEXT_FIELDS = ("trainer", "trainer_version", "gpu", "started_at", "finished_at", "notes", "command")

README = """Parallel training package
=========================

Format: {format}
Reconstruction job: {job}
Frames: {frames}

images/                  the frames Parallel accepted (blur and duplicate filtered), as COLMAP saw them
colmap/sparse/0/         the COLMAP sparse model in text form (cameras, images, points3D)
manifest.json            the SHA-256 of every file above and the job it came from

Train a Gaussian splat from this package with the notebook that Parallel provides
(Reconstruction jobs -> Train on a free GPU -> Download notebook). It keeps the splat in the
reconstruction's own coordinate frame, so Parallel can place it with the scene's existing alignment.
Do not re-run COLMAP and do not re-orient or re-scale the data: a splat in a different frame is
refused on import.
"""


def _stage(job: ReconstructionJob, name: str) -> dict:
    return next((s for s in job.stages if s["name"] == name), {})


def _outputs(job: ReconstructionJob) -> tuple[dict[str, str], dict[str, str]]:
    frames = {k: v for k, v in (_stage(job, "frame_filtering").get("outputs") or {}).items()
              if k.startswith("frames_accepted/")}
    colmap = {k: v for k, v in (_stage(job, "pose_estimation").get("outputs") or {}).items()
              if k.startswith("colmap/sparse_txt/") and k.endswith(".txt")}
    return frames, colmap


def _completed_job(db, user, job_id: str, action: Action) -> ReconstructionJob:
    job = _job(db, user, job_id, action)
    frames, colmap = _outputs(job)
    if job.status != "completed" or not job.scene_id or not frames or not colmap:
        raise ApiError(409, "reconstruction_not_ready",
                       "The reconstruction must have completed, with its frames and COLMAP model kept, "
                       "before a training package can be made or a splat imported")
    return job


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


@router.get("/external-training/notebook")
def notebook(_: CurrentUser) -> Response:
    """The Colab/Kaggle notebook that trains a splat from a training package."""
    return Response(NOTEBOOK.read_bytes(), media_type="application/x-ipynb+json",
                    headers={"Content-Disposition": 'attachment; filename="parallel_splat_training.ipynb"',
                             "Cache-Control": "no-store"})


@router.post("/reconstructions/{job_id}/training-package", response_model=AssetOut, status_code=201)
def training_package(job_id: str, user: CurrentUser, db: DB):
    """Build (or return) the training package of a completed reconstruction, stored as a hashed asset."""
    job = _completed_job(db, user, job_id, Action.edit)
    name = f"parallel-training-{job.id}.zip"
    existing = db.scalar(select(SourceAsset).where(SourceAsset.project_id == job.project_id,
                                                   SourceAsset.filename == name)
                         .order_by(SourceAsset.created_at.desc()))
    if existing and (job.finished_at is None or existing.created_at >= job.finished_at):
        return existing
    frames, colmap = _outputs(job)
    storage = get_storage()
    image_names = sorted(k.split("/", 1)[1] for k in frames)
    with tempfile.TemporaryDirectory() as td:
        zpath = Path(td) / name
        files: list[dict] = []
        with zipfile.ZipFile(zpath, "w") as z:
            for rel, key in sorted(frames.items()):
                data = storage.get_bytes(key)
                arc = f"images/{rel.split('/', 1)[1]}"
                z.writestr(zipfile.ZipInfo(arc, (2020, 1, 1, 0, 0, 0)), data, compress_type=zipfile.ZIP_STORED)
                files.append({"path": arc, "sha256": _sha(data), "bytes": len(data)})
            for rel, key in sorted(colmap.items()):
                data = storage.get_bytes(key)
                arc = f"colmap/sparse/0/{rel.rsplit('/', 1)[1]}"
                z.writestr(zipfile.ZipInfo(arc, (2020, 1, 1, 0, 0, 0)), data, compress_type=zipfile.ZIP_DEFLATED)
                files.append({"path": arc, "sha256": _sha(data), "bytes": len(data)})
            listed = {Path(f["path"]).name for f in files if f["path"].startswith("images/")}
            reg = storage.get_bytes(colmap["colmap/sparse_txt/images.txt"]).decode().splitlines()
            registered = [ln.split()[9] for ln in reg if ln and not ln.startswith("#") and len(ln.split()) >= 10
                          and ln.split()[9].lower().endswith((".jpg", ".jpeg", ".png"))]
            missing = [n for n in registered if n not in listed]
            if missing:
                raise ApiError(409, "frames_missing", f"{len(missing)} registered frames are missing from the "
                                                      f"stored reconstruction (for example {missing[0]})")
            manifest = {
                "format": PACKAGE_FORMAT, "job_id": job.id, "project_id": job.project_id, "scene_id": job.scene_id,
                "coordinate_frame": "capture (COLMAP reconstruction frame; do not re-orient or re-scale)",
                "frames": len(image_names), "registered_frames": len(registered),
                "colmap_camera_model": next((ln.split()[1] for ln in storage.get_bytes(
                    colmap["colmap/sparse_txt/cameras.txt"]).decode().splitlines() if ln and not ln.startswith("#")),
                    None),
                "files": files, "created_at": utcnow().isoformat(),
            }
            mbytes = json.dumps(manifest, indent=1).encode()
            z.writestr("manifest.json", mbytes)
            z.writestr("README.txt", README.format(format=PACKAGE_FORMAT, job=job.id, frames=len(image_names)))
        aid = new_id()
        key = f"projects/{job.project_id}/assets/{aid}/{name}"
        storage.put_file(key, zpath)
        asset = SourceAsset(
            id=aid, project_id=job.project_id, capture_batch_id=job.capture_batch_id, kind="archive", filename=name,
            media_type="application/zip", size_bytes=zpath.stat().st_size, sha256=_file_sha(zpath), storage_key=key,
            status="verified", uploaded_by=user.id,
            inspection={"generated_by": "training package", "job_id": job.id, "format": PACKAGE_FORMAT,
                        "frames": len(image_names), "manifest_sha256": _sha(mbytes)})
        db.add(asset)
        db.flush()
    audit(db, action="reconstruction.training_package_created", object_type="source_asset", object_id=asset.id,
          actor_id=user.id, project_id=job.project_id, details={"job": job.id, "frames": len(image_names)})
    return asset


def _sparse_points(job: ReconstructionJob) -> np.ndarray:
    key = (_stage(job, "pose_estimation").get("outputs") or {}).get("colmap/sparse_txt/points3D.txt")
    if not key:
        raise ApiError(409, "reconstruction_not_ready", "The reconstruction's sparse points are not available")
    rows = [ln.split()[1:4] for ln in get_storage().get_bytes(key).decode().splitlines()
            if ln and not ln.startswith("#")]
    return np.array(rows, dtype=float).reshape(-1, 3)


def _read_info(upload: UploadFile | None, job: ReconstructionJob, package: SourceAsset | None) -> dict:
    if upload is None:
        return {}
    raw = upload.file.read(MAX_INFO_BYTES + 1)
    if len(raw) > MAX_INFO_BYTES:
        raise ApiError(422, "training_info_invalid", "The training information file is larger than 64 KiB")
    try:
        info = json.loads(raw)
    except ValueError as exc:
        raise ApiError(422, "training_info_invalid", f"The training information is not valid JSON: {exc}") from exc
    if not isinstance(info, dict):
        raise ApiError(422, "training_info_invalid", "The training information must be a JSON object")
    if info.get("job_id") not in (None, job.id):
        raise ApiError(422, "package_mismatch", "This splat was trained from a different reconstruction's package")
    stated = info.get("package_manifest_sha256")
    expected = (package.inspection or {}).get("manifest_sha256") if package else None
    if stated and expected and stated != expected:
        raise ApiError(422, "package_mismatch", "The splat was trained from a package that differs from the one "
                                                "Parallel issued for this reconstruction")
    out = {k: str(info[k])[:500] for k in INFO_TEXT_FIELDS if info.get(k) not in (None, "")}
    if isinstance(info.get("iterations"), int | float):
        out["iterations"] = str(int(info["iterations"]))
    out["verified_package"] = "yes" if stated and expected and stated == expected else "not stated"
    return out


@router.post("/reconstructions/{job_id}/import-splat", status_code=201)
def import_splat(job_id: str, user: CurrentUser, db: DB, splat: UploadFile = File(...),
                 training_info: UploadFile | None = File(None)):
    """Attach a Gaussian splat trained outside Parallel to the reconstruction's scene as a new revision."""
    job = _completed_job(db, user, job_id, Action.edit)
    filename = Path(splat.filename or "splat.ply").name
    if not filename.lower().endswith(".ply"):
        raise ApiError(422, "splat_invalid", "Only .ply Gaussian splat files can be imported")
    scene = db.get(Scene, job.scene_id)
    cur = db.get(SceneRevision, scene.current_revision_id) if scene and scene.current_revision_id else None
    if scene is None or cur is None:
        raise ApiError(409, "reconstruction_not_ready", "The reconstruction has no scene to attach a splat to")
    content = SceneRevisionContent.model_validate(cur.content)
    name = f"parallel-training-{job.id}.zip"
    package = db.scalar(select(SourceAsset).where(SourceAsset.project_id == job.project_id,
                                                  SourceAsset.filename == name)
                        .order_by(SourceAsset.created_at.desc()))
    info = _read_info(training_info, job, package)
    sparse = _sparse_points(job)
    guard_storage(db, user)
    limit = upload_limit(user)
    with received_file(splat, limit) as tmp:
        try:
            summary, xyz = splat_check.validate(tmp)
            check = splat_check.alignment_check(xyz, sparse)
        except splat_check.SplatError as exc:
            raise ApiError(422, "splat_invalid", str(exc)) from exc
        if not check["passed"]:
            raise ApiError(
                422, "splat_not_aligned",
                "The splat is not in the same coordinate frame as this reconstruction, so it would not line up "
                "with the scene. Train it from the unchanged training package with the supplied notebook, which "
                "keeps the reconstruction's orientation and scale.", check)
        digest = _file_sha(tmp)
        have = {a.id for a in db.scalars(select(SourceAsset).where(
            SourceAsset.id.in_([v.asset_id for v in content.visual_assets])))
            if a.sha256 == digest}
        if have:
            raise ApiError(409, "already_imported", "This exact splat file is already part of the scene")
        asset = store_upload(db, project_id=job.project_id, user_id=user.id, file_path=tmp, filename=filename,
                             batch_id=job.capture_batch_id, derived_from_id=package.id if package else None,
                             max_bytes=limit)
    trainer = info.get("trainer") or "a Gaussian splat trainer (not stated)"
    gpu = info.get("gpu")
    its = info.get("iterations")
    notes = [
        f"Trained outside Parallel ({trainer}{', ' + gpu if gpu else ''}"
        f"{', ' + its + ' iterations' if its else ''}) and imported by a Parallel user; Parallel did not run "
        "or reproduce this training.",
        f"{summary.count:,} Gaussians, colour degree {summary.sh_degree}. Alignment check passed: median distance "
        f"from sparse points to nearest Gaussian {check['median_distance_fraction_of_spread']:.2%} of the scene "
        f"spread; spread ratio {check['spread_ratio_splat_to_sparse']}.",
        "Appearance only: not solid or walkable geometry, and not evidence of scale or measurement quality.",
    ]
    notes += [f"Note from the trainer: {info['notes']}"] if info.get("notes") else []
    visual = VisualAsset(
        id=f"gaussian_splat_ext_{asset.id[:8]}", asset_id=asset.id, kind=VisualAssetKind.gaussian_splat, format="ply",
        frame="capture", label="Gaussian splat (trained outside Parallel)", quality_notes=notes,
        provenance=Provenance(
            kind=ProvenanceKind.imported, source=trainer, source_ref=asset.id,
            derived_from=[package.id] if package else [], recorded_by=user.id, recorded_at=utcnow(),
            note="; ".join(f"{k}: {v}" for k, v in info.items()) or "No training information was supplied"))
    new = content.model_copy(update={"visual_assets": [*content.visual_assets, visual]})
    rev = create_scene_revision(db, scene, new, user.id, message="Imported a splat trained outside Parallel",
                                parent_id=cur.id)
    audit(db, action="scene.splat_imported", object_type="scene_revision", object_id=rev.id, actor_id=user.id,
          project_id=job.project_id, details={"job": job.id, "asset": asset.id, "sha256": asset.sha256,
                                              "gaussians": summary.count, "alignment": check, "training": info})
    return {"scene_id": scene.id, "scene_revision_id": rev.id, "visual_id": visual.id, "asset_id": asset.id,
            "gaussians": summary.count, "alignment_check": check, "training": info}

