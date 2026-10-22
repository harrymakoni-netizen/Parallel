from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select

from parallel_core.access import Action, require_project
from parallel_core.audit import record as audit
from parallel_core.db import new_id
from parallel_core.models import CaptureBatch, SourceAsset
from parallel_core.settings import get_settings
from parallel_core.storage import LocalStorage, StorageError, get_storage, verify_blob

from ..deps import DB, CurrentUser, Paging
from ..errors import ApiError
from ..schemas import AssetOut, CaptureBatchIn, CaptureBatchOut, PageOut, SignedUrlOut
from ..services.inspect import RejectedUpload, inspect

router = APIRouter(tags=["assets"])


@router.post("/projects/{project_id}/capture-batches", response_model=CaptureBatchOut, status_code=201)
def create_batch(project_id: str, body: CaptureBatchIn, user: CurrentUser, db: DB):
    require_project(db, user, project_id, Action.edit)
    b = CaptureBatch(
        project_id=project_id,
        name=body.name,
        kind=body.kind,
        notes=body.notes,
        captured_at=body.captured_at,
        created_by=user.id,
    )
    db.add(b)
    db.flush()
    audit(
        db,
        action="capture_batch.created",
        object_type="capture_batch",
        object_id=b.id,
        actor_id=user.id,
        project_id=project_id,
    )
    return b


@router.get("/projects/{project_id}/capture-batches", response_model=list[CaptureBatchOut])
def list_batches(project_id: str, user: CurrentUser, db: DB):
    require_project(db, user, project_id)
    return db.scalars(
        select(CaptureBatch).where(CaptureBatch.project_id == project_id).order_by(CaptureBatch.created_at)
    ).all()


@contextmanager
def received_file(file: UploadFile) -> Iterator[Path]:
    """Spool an upload to a temporary file, enforcing the upload size limit."""
    limit = get_settings().max_upload_bytes
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "upload"
        size = 0
        with open(tmp, "wb") as out:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise ApiError(413, "too_large", f"Upload exceeds the {limit} byte limit")
                out.write(chunk)
        yield tmp


def store_upload(
    db,
    *,
    project_id: str,
    user_id: str,
    file_path: Path,
    filename: str,
    batch_id: str | None,
    sensitivity: str = "normal",
    derived_from_id: str | None = None,
) -> SourceAsset:
    """Verify, inspect, hash and store an uploaded file; returns the SourceAsset."""
    try:
        kind, info = inspect(file_path, filename)
    except RejectedUpload as exc:
        raise ApiError(422, "upload_rejected", str(exc)) from exc
    except Exception as exc:  # parser errors on malformed content
        raise ApiError(422, "upload_rejected", f"Could not inspect file: {exc}") from exc
    aid = new_id()
    safe = Path(filename).name.replace("/", "_")[:200] or "upload"
    key = f"projects/{project_id}/assets/{aid}/{safe}"
    storage = get_storage()
    with open(file_path, "rb") as fh:
        size, sha = storage.put_stream(key, fh, "application/octet-stream", get_settings().max_upload_bytes)
    media = _media_type(safe)
    a = SourceAsset(
        id=aid,
        project_id=project_id,
        capture_batch_id=batch_id,
        kind=kind,
        filename=safe,
        media_type=media,
        size_bytes=size,
        sha256=sha,
        storage_key=key,
        status="verified",
        inspection=info,
        sensitivity=sensitivity,
        derived_from_id=derived_from_id,
        uploaded_by=user_id,
    )
    db.add(a)
    db.flush()
    return a


def _media_type(name: str) -> str:
    import mimetypes

    return mimetypes.guess_type(name)[0] or "application/octet-stream"


@router.post("/projects/{project_id}/assets", response_model=AssetOut, status_code=201)
def upload_asset(
    project_id: str,
    user: CurrentUser,
    db: DB,
    file: UploadFile = File(...),
    capture_batch_id: str | None = Form(None),
):
    require_project(db, user, project_id, Action.edit)
    if capture_batch_id:
        b = db.get(CaptureBatch, capture_batch_id)
        if b is None or b.project_id != project_id:
            raise ApiError(404, "not_found", "Capture batch not found")
    with received_file(file) as tmp:
        a = store_upload(
            db,
            project_id=project_id,
            user_id=user.id,
            file_path=tmp,
            filename=file.filename or "upload",
            batch_id=capture_batch_id,
        )
    audit(
        db,
        action="asset.uploaded",
        object_type="source_asset",
        object_id=a.id,
        actor_id=user.id,
        project_id=project_id,
        details={"filename": a.filename, "sha256": a.sha256, "size": a.size_bytes, "kind": a.kind},
    )
    return a


@router.get("/projects/{project_id}/assets", response_model=PageOut[AssetOut])
def list_assets(
    project_id: str,
    user: CurrentUser,
    db: DB,
    page: Paging,
    kind: str | None = None,
    capture_batch_id: str | None = None,
):
    require_project(db, user, project_id)
    q = select(SourceAsset).where(SourceAsset.project_id == project_id, SourceAsset.sensitivity == "normal")
    if kind:
        q = q.where(SourceAsset.kind == kind)
    if capture_batch_id:
        q = q.where(SourceAsset.capture_batch_id == capture_batch_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    items = db.scalars(q.order_by(SourceAsset.created_at.desc()).limit(page.limit).offset(page.offset)).all()
    return PageOut(
        items=[AssetOut.model_validate(a) for a in items], total=total, limit=page.limit, offset=page.offset
    )


def _asset_for(db, user, asset_id: str) -> SourceAsset:
    a = db.get(SourceAsset, asset_id)
    if a is None:
        raise ApiError(404, "not_found", "Asset not found")
    require_project(db, user, a.project_id)
    if a.sensitivity == "evidence":
        raise ApiError(404, "not_found", "Evidence media is only available through its case")
    return a


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: str, user: CurrentUser, db: DB):
    return _asset_for(db, user, asset_id)


@router.get("/assets/{asset_id}/url", response_model=SignedUrlOut)
def asset_url(asset_id: str, user: CurrentUser, db: DB):
    a = _asset_for(db, user, asset_id)
    s = get_settings()
    return SignedUrlOut(
        url=get_storage().signed_url(a.storage_key, a.filename), expires_in_s=s.signed_url_seconds
    )


@router.get("/blobs", include_in_schema=False)
def blob(key: str = Query(...), exp: int = Query(...), sig: str = Query(...), filename: str | None = None):
    """Serve a signed object from local storage (S3 deployments use presigned URLs instead)."""
    s = get_settings()
    if not verify_blob(s.secret_key, key, exp, sig):
        raise ApiError(403, "invalid_signature", "Link expired or invalid")
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        return RedirectResponse(storage.signed_url(key, filename))
    try:
        path = storage.path(key)
    except StorageError as exc:
        raise ApiError(400, "invalid_key", str(exc)) from exc
    if not path.exists():
        raise ApiError(404, "not_found", "Object not found")
    return FileResponse(
        path,
        filename=filename,
        media_type=_media_type(filename or path.name),
        headers={"Cache-Control": "private, max-age=300"},
    )
