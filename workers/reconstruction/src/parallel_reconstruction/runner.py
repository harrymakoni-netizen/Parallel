"""Reconstruction job runner: executes pipeline stages with checkpoints, heartbeats and scene publication."""

from __future__ import annotations

import json
import shutil
import socket
import time
import traceback
from pathlib import Path

from sqlalchemy import select

from parallel_contracts.coordinates import CoordinateFrame, FrameKind, SceneCoordinates, Transform
from parallel_contracts.provenance import Provenance, ProvenanceKind, SourcedQuantity
from parallel_contracts.quality import QualityAssessment, QualityState
from parallel_contracts.scene import Measurement, MeasurementTolerance, SceneRevisionContent, VisualAsset
from parallel_core import events
from parallel_core.audit import record as audit
from parallel_core.db import new_id, session_scope, utcnow
from parallel_core.models import CaptureBatch, ReconstructionJob, Scene, SourceAsset
from parallel_core.revisions import create_scene_revision
from parallel_core.settings import get_settings
from parallel_core.storage import get_storage
from parallel_scene.measurement import evaluate

from . import pipeline
from .pipeline import (
    STAGE_FUNCS,
    STAGE_OUTPUTS,
    STAGES,
    InputAsset,
    PipelineContext,
    PipelineError,
    StageSkipped,
)


def _set_stage(job_id: str, name: str, **fields) -> None:
    with session_scope() as db:
        job = db.get(ReconstructionJob, job_id, with_for_update=True)
        stages = [dict(s) for s in job.stages]  # type: ignore[union-attr]
        for s in stages:
            if s["name"] == name:
                s.update(fields)
        job.stages = stages  # type: ignore[union-attr]
        job.heartbeat_at = utcnow()  # type: ignore[union-attr]
    events.publish("reconstruction", job_id, {"job_id": job_id, "stage": name, **{k: v for k, v in fields.items()
                                                                                  if k in ("status", "message")}})


def _checkpoint(job_id: str, workdir: Path, stage: str) -> dict[str, str]:
    storage = get_storage()
    keys = {}
    for d in STAGE_OUTPUTS.get(stage, []):
        root = workdir / d
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if f.is_file():
                rel = f.relative_to(workdir).as_posix()
                key = f"reconstructions/{job_id}/checkpoints/{rel}"
                storage.put_file(key, f)
                keys[rel] = key
    return keys


def _restore(workdir: Path, outputs: dict[str, str]) -> None:
    storage = get_storage()
    for rel, key in outputs.items():
        dest = workdir / rel
        if not dest.exists():
            storage.get_to_path(key, dest)


def new_stage_records() -> list[dict]:
    return [{"name": s, "status": "pending", "started_at": None, "finished_at": None, "message": None, "data": {},
             "outputs": {}} for s in STAGES]


def execute_job(job_id: str) -> str:
    s = get_settings()
    with session_scope() as db:
        job = db.get(ReconstructionJob, job_id, with_for_update=True)
        if job is None:
            return "missing"
        if job.status not in ("queued", "resuming"):
            return f"skipped:{job.status}"
        job.status = "running"
        job.started_at = job.started_at or utcnow()
        job.heartbeat_at = utcnow()
        settings = {**pipeline.DEFAULT_SETTINGS, **(job.settings or {})}
        assets = [InputAsset(a.id, a.filename, a.kind, a.sha256, a.storage_key, a.inspection)
                  for a in db.scalars(select(SourceAsset).where(SourceAsset.capture_batch_id == job.capture_batch_id))]
        stages = [dict(x) for x in job.stages]
        project_id = job.project_id
    workdir = Path(s.workspace_root) / "reconstructions" / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    log = workdir / "pipeline.log"
    storage = get_storage()

    def fetch(key: str, dest: Path) -> Path:
        if not dest.exists():
            storage.get_to_path(key, dest)
        return dest

    def cancelled() -> bool:
        with session_scope() as db:
            return bool(db.scalar(select(ReconstructionJob.cancel_requested).where(ReconstructionJob.id == job_id)))

    def heartbeat(stage: str, frac: float, msg: str) -> None:
        _set_stage(job_id, stage, message=msg)

    image_assets = [a for a in assets if a.kind in ("image", "video")]
    aux = {a.filename: a for a in assets if a.filename in ("camera_positions.csv", "reference_points.csv")}
    ctx = PipelineContext(job_id=job_id, project_id=project_id, workdir=workdir, settings=settings, assets=image_assets,
                          fetch=fetch, log=log, heartbeat=heartbeat, cancelled=cancelled)
    for name, a in aux.items():
        fetch(a.storage_key, workdir / "inputs" / name)
    for st in stages:
        if st["status"] in ("completed", "skipped"):
            ctx.data[st["name"]] = st.get("data") or {}
            _restore(workdir, st.get("outputs") or {})
    try:
        for st in stages:
            name = st["name"]
            if st["status"] in ("completed", "skipped"):
                continue
            if cancelled():
                raise PipelineError("cancelled")
            _set_stage(job_id, name, status="running", started_at=utcnow().isoformat(), message=None)
            t0 = time.time()
            try:
                data = publish_scene(job_id, ctx) if name == "scene_publication" else STAGE_FUNCS[name](ctx)
                status, message = "completed", None
            except StageSkipped as sk:
                data, status, message = sk.data, "skipped", str(sk)
            ctx.data[name] = data
            outputs = _checkpoint(job_id, workdir, name) if status == "completed" else {}
            _set_stage(job_id, name, status=status, finished_at=utcnow().isoformat(), message=message,
                       data=json.loads(json.dumps(data, default=str)), outputs=outputs,
                       duration_s=round(time.time() - t0, 2))
        with session_scope() as db:
            job = db.get(ReconstructionJob, job_id)
            job.status, job.finished_at = "completed", utcnow()  # type: ignore[union-attr]
        events.publish("reconstruction", job_id, {"job_id": job_id, "status": "completed"})
        return "completed"
    except Exception as exc:  # stage failure: record it; the job can be resumed from the last checkpoint
        msg = str(exc) if isinstance(exc, PipelineError | pipeline.colmap.ColmapError) else f"{type(exc).__name__}: {exc}"
        with session_scope() as db:
            job = db.get(ReconstructionJob, job_id)
            running = next((x for x in job.stages if x["status"] == "running"), None)  # type: ignore[union-attr]
            if running:
                stages2 = [dict(x) for x in job.stages]  # type: ignore[union-attr]
                for x in stages2:
                    if x["name"] == running["name"]:
                        x.update(status="failed", finished_at=utcnow().isoformat(), message=msg[:2000])
                job.stages = stages2  # type: ignore[union-attr]
            job.status = "cancelled" if msg == "cancelled" else "failed"  # type: ignore[union-attr]
            job.error = msg[:4000] if msg != "cancelled" else None  # type: ignore[union-attr]
            job.finished_at = utcnow()  # type: ignore[union-attr]
        with open(log, "a") as fh:
            fh.write(traceback.format_exc())
        events.publish("reconstruction", job_id, {"job_id": job_id, "status": "failed", "error": msg[:500]})
        return "failed"


def _store_output(db, project_id: str, batch_id: str | None, path: Path, kind: str, label: str) -> SourceAsset:
    import hashlib

    aid = new_id()
    key = f"projects/{project_id}/assets/{aid}/{path.name}"
    get_storage().put_file(key, path)
    a = SourceAsset(id=aid, project_id=project_id, capture_batch_id=batch_id, kind=kind, filename=path.name,
                    media_type="application/octet-stream" if path.suffix == ".ply" else "application/json",
                    size_bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), storage_key=key,
                    status="verified", inspection={"generated_by": "reconstruction pipeline", "label": label})
    db.add(a)
    db.flush()
    return a


def publish_scene(job_id: str, ctx: PipelineContext) -> dict:
    qa = ctx.data["quality_assessment"]
    so = ctx.data["scale_orientation"]
    pe = ctx.data["pose_estimation"]
    export = ctx.workdir / "export"
    settings = ctx.settings
    with session_scope() as db:
        job = db.get(ReconstructionJob, job_id)
        batch = db.get(CaptureBatch, job.capture_batch_id)  # type: ignore[union-attr]
        synthetic = bool(batch.synthetic)  # type: ignore[union-attr]
        prov_kind = ProvenanceKind.synthetic if synthetic else ProvenanceKind.algorithm_estimate
        pc = _store_output(db, ctx.project_id, batch.id, export / "sparse_points.ply", "point_cloud",  # type: ignore[union-attr]
                           "COLMAP sparse point cloud (canonical frame)")
        gi = _store_output(db, ctx.project_id, batch.id, export / "gaussian_initialisation.ply", "gaussian",  # type: ignore[union-attr]
                           "UNTRAINED Gaussian initialisation from SfM points")
        _store_output(db, ctx.project_id, batch.id, export / "camera_poses.json", "model", "Camera poses (capture frame)")  # type: ignore[union-attr]
        visual = [
            VisualAsset(id="sparse_points", asset_id=pc.id, kind="point_cloud", format="ply", label="Sparse point cloud (COLMAP)",
                        quality_notes=[f"{pe['points']} points, mean reprojection error {pe['mean_reprojection_error_px']} px"]),
            VisualAsset(id="gaussian_init", asset_id=gi.id, kind="gaussian_initialisation", format="ply",
                        label="Gaussian initialisation (untrained)",
                        quality_notes=["Untrained: SfM points rendered as isotropic Gaussians. "
                                       "Splatfacto training requires a GPU worker."]),
        ]
        trained = export.parent / "splatfacto" / "export" / "splat.ply"
        if trained.exists():
            ts = _store_output(db, ctx.project_id, batch.id, trained, "gaussian", "Trained Splatfacto Gaussian splat")  # type: ignore[union-attr]
            visual.insert(0, VisualAsset(id="gaussian_splat", asset_id=ts.id, kind="gaussian_splat", format="ply",
                                         label="Gaussian splat (Splatfacto)", frame="capture"))
        coords = SceneCoordinates(
            frames=[CoordinateFrame(id="canonical", kind=FrameKind.canonical),
                    CoordinateFrame(id="capture", kind=FrameKind.capture, description="COLMAP reconstruction frame")],
            transforms=[Transform(from_frame="capture", to_frame="canonical", matrix=so["matrix"], method=so["method"],
                                  residual_rms_m=so["residual_rms_m"],
                                  provenance=Provenance(kind=ProvenanceKind.algorithm_estimate, source="Parallel scale_orientation",
                                                        note=settings.get("reference_note")))],
        )
        tol = MeasurementTolerance(purpose=settings["tolerance_purpose"], max_abs_error_m=float(settings["tolerance_abs_m"]))
        measurements = []
        ref_prov = Provenance(kind=ProvenanceKind.synthetic if synthetic else ProvenanceKind.measured,
                              source=settings.get("reference_source", "reference_points.csv"),
                              note=settings.get("reference_note"))
        for i, c in enumerate(qa.get("measurement_checks", [])):
            m = Measurement(id=f"check_{i + 1}", label=c["label"], purpose="independent_check", point_a=c["point_a"],
                            point_b=c["point_b"], reference_distance=SourcedQuantity(value=c["reference_m"], unit="m",
                                                                                     provenance=ref_prov),
                            source=c["source"], created_at=utcnow())
            measurements.append(evaluate(m, tol))
        quality = QualityAssessment()
        now = utcnow()
        if qa["visual_reconstruction_available"]:
            quality.mark(QualityState.visual_reconstruction_available,
                         f"{pe['images_registered']}/{pe['images_input']} frames registered; {pe['points']} points; "
                         f"mean reprojection error {pe['mean_reprojection_error_px']} px", "reconstruction pipeline", now)
        if so["scale_established"]:
            quality.mark(QualityState.scale_established,
                         f"{so['method']}; {so['pairs']} reference pairs; RMS residual {so['residual_rms_m']:.4f} m",
                         "reconstruction pipeline", now)
        if measurements and all(m.within_tolerance for m in measurements):
            quality.mark(QualityState.measurements_checked,
                         f"{len(measurements)} independent distance checks within {tol.max_abs_error_m} m ({tol.purpose})",
                         "reconstruction pipeline", now)
        scene = db.get(Scene, job.scene_id) if job.scene_id else None  # type: ignore[union-attr]
        if scene is None:
            scene = Scene(project_id=ctx.project_id, name=settings.get("scene_name") or f"Reconstruction of {batch.name}")  # type: ignore[union-attr]
            db.add(scene)
            db.flush()
            job.scene_id = scene.id  # type: ignore[union-attr]
        content = SceneRevisionContent(
            scene_id=scene.id, coordinates=coords, source_asset_ids=[a.id for a in ctx.assets], visual_assets=visual,
            measurements=measurements, tolerance=tol, quality=quality,
            provenance=Provenance(kind=prov_kind, source=f"Parallel reconstruction job {job_id} (COLMAP {pe['colmap_version']})",
                                  note="Synthetic capture" if synthetic else None),
        )
        rev = create_scene_revision(db, scene, content, job.created_by, message=f"Reconstruction job {job_id}",  # type: ignore[union-attr]
                                    parent_id=scene.current_revision_id)
        job.result_scene_revision_id = rev.id  # type: ignore[union-attr]
        audit(db, action="scene.published_from_reconstruction", object_type="scene_revision", object_id=rev.id,
              actor_id=job.created_by, project_id=ctx.project_id, details={"job": job_id})  # type: ignore[union-attr]
        return {"scene_id": scene.id, "scene_revision_id": rev.id, "measurements": [m.model_dump(mode="json") for m in measurements],
                "quality": [r.model_dump(mode="json") for r in quality.records], "worker": socket.gethostname()}


def reset_for_resume(job: ReconstructionJob, from_stage: str | None = None) -> None:
    """Mark failed/cancelled stages (or everything from ``from_stage``) pending so the job resumes from checkpoints."""
    stages = [dict(x) for x in job.stages]
    reset = False
    for x in stages:
        if from_stage and x["name"] == from_stage:
            reset = True
        if reset or x["status"] in ("failed", "running"):
            x.update(status="pending", started_at=None, finished_at=None, message=None)
            if reset:
                x.update(data={}, outputs={})
    job.stages = stages
    job.status = "resuming"
    job.error = None
    job.cancel_requested = False
    job.finished_at = None


def cleanup(job_id: str) -> None:
    shutil.rmtree(Path(get_settings().workspace_root) / "reconstructions" / job_id, ignore_errors=True)
