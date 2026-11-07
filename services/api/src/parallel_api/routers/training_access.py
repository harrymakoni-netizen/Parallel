"""Access codes for training a splat on a borrowed GPU without moving files by hand.

A person with edit access to a reconstruction creates a code. The training notebook (running on Google's
or a rented machine) uses it to download that job's training package and to send the trained splat back,
which is imported exactly as a manual upload would be: same checks, same provenance, recorded as imported by
the person who created the code.

The code is deliberately narrow, because it leaves Parallel: it works for one job and only for these two
requests, it expires, it can be revoked, only its SHA-256 is stored, and every use re-checks that its
creator still has edit access to the project. It is sent in its own header and is never accepted as a
sign-in token.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, File, Header, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select

from parallel_core.access import Action, require_project
from parallel_core.audit import record as audit
from parallel_core.db import utcnow
from parallel_core.models import SourceAsset, TrainingAccessCode, User
from parallel_core.settings import get_settings
from parallel_core.storage import LocalStorage, get_storage

from ..deps import DB, CurrentUser
from ..errors import ApiError
from .external_training import _completed_job, import_into, training_package

router = APIRouter(tags=["external-training"])

PREFIX = "ptk_"
HEADER = "X-Parallel-Training-Code"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _out(c: TrainingAccessCode) -> dict:
    now = utcnow()
    state = "revoked" if c.revoked_at else "expired" if c.expires_at <= now else "active"
    return {"id": c.id, "job_id": c.job_id, "created_by": c.created_by, "created_at": c.created_at,
            "expires_at": c.expires_at, "revoked_at": c.revoked_at, "last_used_at": c.last_used_at, "state": state}


@router.post("/reconstructions/{job_id}/training-codes", status_code=201)
def create_code(job_id: str, user: CurrentUser, db: DB):
    """Create an access code for this job's training notebook. The code is returned once and never again."""
    job = _completed_job(db, user, job_id, Action.edit)
    package = training_package(job_id, user, db)  # built now so the notebook's first request is quick
    token = PREFIX + secrets.token_urlsafe(32)
    code = TrainingAccessCode(job_id=job.id, project_id=job.project_id, created_by=user.id, token_hash=_hash(token),
                              expires_at=utcnow() + timedelta(hours=get_settings().training_code_hours))
    db.add(code)
    db.flush()
    audit(db, action="reconstruction.training_code_created", object_type="training_access_code", object_id=code.id,
          actor_id=user.id, project_id=job.project_id, details={"job": job.id, "expires_at": code.expires_at.isoformat()})
    return {**_out(code), "token": token, "package_asset_id": package.id, "header": HEADER}


@router.get("/reconstructions/{job_id}/training-codes")
def list_codes(job_id: str, user: CurrentUser, db: DB):
    job = _completed_job(db, user, job_id, Action.edit)
    return [_out(c) for c in db.scalars(select(TrainingAccessCode).where(TrainingAccessCode.job_id == job.id)
                                        .order_by(TrainingAccessCode.created_at.desc()))]


@router.delete("/training-codes/{code_id}")
def revoke_code(code_id: str, user: CurrentUser, db: DB):
    c = db.get(TrainingAccessCode, code_id)
    if c is None:
        raise ApiError(404, "not_found", "Access code not found")
    require_project(db, user, c.project_id, Action.edit)
    if c.revoked_at is None:
        c.revoked_at = utcnow()
        audit(db, action="reconstruction.training_code_revoked", object_type="training_access_code", object_id=c.id,
              actor_id=user.id, project_id=c.project_id, details={"job": c.job_id})
    return _out(c)


def _redeem(db, token: str | None) -> tuple[TrainingAccessCode, User]:
    """The code and its creator, or a refusal. Unknown, expired and revoked codes get the same answer."""
    if not token or not token.startswith(PREFIX):
        raise ApiError(401, "invalid_training_code", f"A training access code is required in the {HEADER} header")
    c = db.scalar(select(TrainingAccessCode).where(TrainingAccessCode.token_hash == _hash(token)))
    creator = db.get(User, c.created_by) if c else None
    if c is None or c.revoked_at or c.expires_at <= utcnow() or creator is None or not creator.is_active:
        raise ApiError(401, "invalid_training_code",
                       "This access code is not valid: it may have expired or been revoked. Create a new one on the "
                       "reconstruction job in Parallel.")
    c.last_used_at = utcnow()
    return c, creator


Code = Annotated[str | None, Header(alias=HEADER)]


@router.get("/training-access/package")
def package_for_code(db: DB, code: Code = None):
    """The training package of the code's job (the same stored, hashed archive as a manual download)."""
    c, creator = _redeem(db, code)
    job = _completed_job(db, creator, c.job_id, Action.edit)  # re-checks the creator's access now
    pkg: SourceAsset = training_package(job.id, creator, db)
    audit(db, action="reconstruction.training_package_fetched", object_type="source_asset", object_id=pkg.id,
          actor_id=creator.id, project_id=job.project_id, details={"job": job.id, "via": {"training_code": c.id}})
    db.commit()
    storage = get_storage()
    if isinstance(storage, LocalStorage):
        return FileResponse(storage.path(pkg.storage_key), filename=pkg.filename, media_type="application/zip",
                            headers={"Cache-Control": "no-store", "X-Package-SHA256": pkg.sha256 or ""})
    return RedirectResponse(storage.signed_url(pkg.storage_key, pkg.filename))


@router.post("/training-access/import-splat", status_code=201)
def import_for_code(db: DB, code: Code = None, splat: UploadFile = File(...),
                    training_info: UploadFile | None = File(None)):
    """Import the trained splat into the code's job, acting for the code's creator."""
    c, creator = _redeem(db, code)
    job = _completed_job(db, creator, c.job_id, Action.edit)
    return import_into(db, creator, job, splat, training_info, via={"training_code": c.id})
