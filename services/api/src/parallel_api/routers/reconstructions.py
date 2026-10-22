"""Capture processing and reconstruction jobs (/api/v1/reconstructions)."""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from parallel_core import events
from parallel_core.access import Action, require_project
from parallel_core.audit import record as audit
from parallel_core.db import SessionLocal
from parallel_core.jobs import TASK_RECONSTRUCT, celery_app
from parallel_core.models import CaptureBatch, ReconstructionJob, Scene, SourceAsset
from parallel_core.settings import get_settings
from parallel_core.storage import get_storage

from ..deps import DB, CurrentUser
from ..errors import ApiError

router = APIRouter(tags=["reconstructions"])

GUIDANCE = {
    "title": "Guided capture",
    "sections": [
        {"heading": "Overlap and coverage", "points": [
            "Move slowly so consecutive frames overlap by roughly 70-80%.",
            "Walk around objects of interest; cover each surface from several viewpoints and heights.",
            "Close loops: return to your starting viewpoint so the reconstruction can be closed."]},
        {"heading": "Light and sharpness", "points": [
            "Use stable, even lighting; avoid strong backlight and moving shadows.",
            "Keep the camera steady: blurred frames are rejected by the Laplacian-variance filter."]},
        {"heading": "Reference measurements", "points": [
            "Place at least two surveyed or tape-measured targets in view (scale reference).",
            "Measure at least one independent distance to check the reconstruction afterwards.",
            "Record measurement sources and their uncertainty; they become provenance in Parallel."]},
        {"heading": "Problem surfaces", "points": [
            "Moving people and vehicles, reflective glass, water and featureless walls reconstruct poorly.",
            "Captured surfaces are not hidden infrastructure: pipe sizes, invert levels and rock properties "
            "must be supplied or explicitly assumed."]},
        {"heading": "Offline capture", "points": [
            "Capture without connectivity and upload later; file hashes are recorded at upload."]},
    ],
}


class ReconstructionIn(BaseModel):
    capture_batch_id: str
    scene_id: str | None = None
    settings: dict = Field(default_factory=dict)


def _job_out(j: ReconstructionJob) -> dict:
    return {"id": j.id, "project_id": j.project_id, "capture_batch_id": j.capture_batch_id, "scene_id": j.scene_id,
            "status": j.status, "stages": j.stages, "settings": j.settings, "error": j.error,
            "result_scene_revision_id": j.result_scene_revision_id, "created_at": j.created_at, "started_at": j.started_at,
            "finished_at": j.finished_at, "heartbeat_at": j.heartbeat_at, "cancel_requested": j.cancel_requested}


@router.get("/capture-guidance")
def guidance() -> dict:
    return GUIDANCE


def enqueue(db, job: ReconstructionJob) -> None:
    db.commit()
    if get_settings().task_always_eager:
        from parallel_reconstruction.runner import execute_job

        execute_job(job.id)
        db.refresh(job)
        return
    res = celery_app.send_task(TASK_RECONSTRUCT, args=[job.id])
    job.celery_task_id = res.id


@router.post("/projects/{project_id}/reconstructions", status_code=201)
def start(project_id: str, body: ReconstructionIn, user: CurrentUser, db: DB):
    require_project(db, user, project_id, Action.edit)
    batch = db.get(CaptureBatch, body.capture_batch_id)
    if batch is None or batch.project_id != project_id:
        raise ApiError(404, "not_found", "Capture batch not found")
    media = db.scalars(select(SourceAsset).where(SourceAsset.capture_batch_id == batch.id,
                                                 SourceAsset.kind.in_(["image", "video"]))).all()
    if not media:
        raise ApiError(422, "no_media", "The capture batch has no photographs or video")
    if body.scene_id:
        sc = db.get(Scene, body.scene_id)
        if sc is None or sc.project_id != project_id:
            raise ApiError(404, "not_found", "Scene not found")
    from parallel_reconstruction.pipeline import DEFAULT_SETTINGS
    from parallel_reconstruction.runner import new_stage_records

    unknown = set(body.settings) - set(DEFAULT_SETTINGS) - {"scene_name", "reference_source", "reference_note"}
    if unknown:
        raise ApiError(422, "unknown_settings", f"Unknown settings: {sorted(unknown)}")
    job = ReconstructionJob(project_id=project_id, capture_batch_id=batch.id, scene_id=body.scene_id, status="queued",
                            stages=new_stage_records(), settings=body.settings, created_by=user.id)
    db.add(job)
    db.flush()
    audit(db, action="reconstruction.started", object_type="reconstruction_job", object_id=job.id, actor_id=user.id,
          project_id=project_id, details={"capture_batch_id": batch.id, "inputs": len(media)})
    enqueue(db, job)
    return _job_out(job)


def _job(db, user, job_id: str, action=Action.view) -> ReconstructionJob:
    j = db.get(ReconstructionJob, job_id)
    if j is None:
        raise ApiError(404, "not_found", "Reconstruction job not found")
    require_project(db, user, j.project_id, action)
    return j


@router.get("/projects/{project_id}/reconstructions")
def list_jobs(project_id: str, user: CurrentUser, db: DB):
    require_project(db, user, project_id)
    return [_job_out(j) for j in db.scalars(select(ReconstructionJob).where(ReconstructionJob.project_id == project_id)
                                            .order_by(ReconstructionJob.created_at.desc()))]


@router.get("/reconstructions/{job_id}")
def get_job(job_id: str, user: CurrentUser, db: DB):
    return _job_out(_job(db, user, job_id))


@router.post("/reconstructions/{job_id}/cancel")
def cancel(job_id: str, user: CurrentUser, db: DB):
    j = _job(db, user, job_id, Action.edit)
    if j.status in ("queued", "resuming"):
        j.status = "cancelled"
    elif j.status == "running":
        j.cancel_requested = True
    audit(db, action="reconstruction.cancel_requested", object_type="reconstruction_job", object_id=j.id, actor_id=user.id,
          project_id=j.project_id)
    return _job_out(j)


@router.post("/reconstructions/{job_id}/resume")
def resume(job_id: str, user: CurrentUser, db: DB, from_stage: str | None = None):
    """Resume from the last valid checkpoint, or restart from a named stage."""
    from parallel_reconstruction.pipeline import STAGES
    from parallel_reconstruction.runner import reset_for_resume

    j = _job(db, user, job_id, Action.edit)
    if j.status not in ("failed", "cancelled", "completed"):
        raise ApiError(409, "not_resumable", f"Job is {j.status}")
    if from_stage and from_stage not in STAGES:
        raise ApiError(422, "unknown_stage", f"Unknown stage {from_stage}")
    reset_for_resume(j, from_stage)
    audit(db, action="reconstruction.resumed", object_type="reconstruction_job", object_id=j.id, actor_id=user.id,
          project_id=j.project_id, details={"from_stage": from_stage})
    enqueue(db, j)
    return _job_out(j)


@router.get("/reconstructions/{job_id}/frames")
def frames(job_id: str, user: CurrentUser, db: DB):
    """Accepted and rejected frame records from the filtering stage."""
    j = _job(db, user, job_id)
    st = next((s for s in j.stages if s["name"] == "frame_filtering"), None)
    key = (st or {}).get("outputs", {}).get("frame_filtering/frame_records.csv")
    if not key:
        raise ApiError(404, "not_found", "Frame records not available yet")
    rows = list(csv.DictReader(io.StringIO(get_storage().get_bytes(key).decode())))
    return {"frames": rows, "summary": (st or {}).get("data", {})}


@router.get("/reconstructions/{job_id}/events")
async def job_events(job_id: str, user: CurrentUser, db: DB):
    _job(db, user, job_id)

    def snap():
        s = SessionLocal()
        try:
            j = s.get(ReconstructionJob, job_id)
            return {"status": j.status, "stages": [{k: x.get(k) for k in ("name", "status", "message")} for x in j.stages]}  # type: ignore[union-attr]
        finally:
            s.close()

    async def stream():
        ps = await run_in_threadpool(events.subscribe, "reconstruction", job_id)
        last = None
        try:
            while True:
                cur = await run_in_threadpool(snap)
                if cur != last:
                    yield f"event: job\ndata: {json.dumps(cur)}\n\n"
                    last = cur
                if cur["status"] in ("completed", "failed", "cancelled"):
                    break
                if await run_in_threadpool(ps.get_message, True, 2.0) is None:
                    yield ": keep-alive\n\n"
        finally:
            await run_in_threadpool(ps.close)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
