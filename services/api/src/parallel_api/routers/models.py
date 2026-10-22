"""Domain models and their revisions (/api/v1/models)."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from parallel_contracts.domain import MODEL_KIND_DISCIPLINE, DomainModelEnvelope
from parallel_contracts.runs import ValidationReport
from parallel_core.access import Action, require_project
from parallel_core.audit import record as audit
from parallel_core.models import DomainModel, DomainModelRevision
from parallel_core.revisions import create_model_revision, publish, update_model_draft
from parallel_engines.registry import all_adapters

from ..deps import DB, CurrentUser
from ..errors import ApiError
from ..schemas import DomainModelIn, DomainModelOut, ModelRevisionOut, RevisionIn

router = APIRouter(tags=["models"])


def validate_envelope(env: DomainModelEnvelope) -> dict:
    """Validate a model with every adapter that supports its discipline (merged, labelled by engine)."""
    reports = {}
    for a in all_adapters():
        if env.discipline.value in a.capability.disciplines:
            reports[a.engine_id] = a.validate_model(env).model_dump(mode="json")
    ok = any(r["ok"] for r in reports.values()) if reports else False
    return {"ok": ok, "by_engine": reports}


@router.get("/models/schemas")
def model_schemas():
    from pydantic import TypeAdapter

    from parallel_contracts.domain import DomainModelContent

    return {
        "model_kinds": MODEL_KIND_DISCIPLINE,
        "envelope": DomainModelEnvelope.model_json_schema(),
        "content": TypeAdapter(DomainModelContent).json_schema(),
    }


@router.get("/projects/{project_id}/models", response_model=list[DomainModelOut])
def list_models(project_id: str, user: CurrentUser, db: DB):
    require_project(db, user, project_id)
    return db.scalars(
        select(DomainModel).where(DomainModel.project_id == project_id).order_by(DomainModel.created_at)
    ).all()


@router.post("/projects/{project_id}/models", response_model=ModelRevisionOut, status_code=201)
def create_model(project_id: str, body: DomainModelIn, user: CurrentUser, db: DB):
    require_project(db, user, project_id, Action.edit)
    env = DomainModelEnvelope.model_validate(body.content)
    dm = DomainModel(project_id=project_id, name=body.name, discipline=env.discipline.value)
    db.add(dm)
    db.flush()
    rev = create_model_revision(
        db, dm, env, user.id, validate_envelope(env), message=body.message or "created"
    )
    audit(
        db,
        action="model.created",
        object_type="domain_model",
        object_id=dm.id,
        actor_id=user.id,
        project_id=project_id,
        details={"discipline": dm.discipline, "revision": rev.number},
    )
    return rev


def _model(db, user, model_id: str, action=Action.view) -> DomainModel:
    dm = db.get(DomainModel, model_id)
    if dm is None:
        raise ApiError(404, "not_found", "Model not found")
    require_project(db, user, dm.project_id, action)
    return dm


@router.get("/models/{model_id}", response_model=DomainModelOut)
def get_model(model_id: str, user: CurrentUser, db: DB):
    return _model(db, user, model_id)


@router.get("/models/{model_id}/revisions", response_model=list[ModelRevisionOut])
def model_revisions(model_id: str, user: CurrentUser, db: DB):
    _model(db, user, model_id)
    return db.scalars(
        select(DomainModelRevision)
        .where(DomainModelRevision.domain_model_id == model_id)
        .order_by(DomainModelRevision.number.desc())
    ).all()


@router.get("/model-revisions/{revision_id}", response_model=ModelRevisionOut)
def get_model_revision(revision_id: str, user: CurrentUser, db: DB):
    rev = db.get(DomainModelRevision, revision_id)
    if rev is None:
        raise ApiError(404, "not_found", "Revision not found")
    _model(db, user, rev.domain_model_id)
    return rev


@router.post("/models/{model_id}/revisions", response_model=ModelRevisionOut, status_code=201)
def save_model_revision(model_id: str, body: RevisionIn, user: CurrentUser, db: DB):
    """Edit a model: updates the current draft in place, or creates a new revision when the current one is published."""
    dm = _model(db, user, model_id, Action.edit)
    env = DomainModelEnvelope.model_validate(body.content)
    if env.discipline.value != dm.discipline:
        raise ApiError(
            422, "discipline_change", "A model's discipline cannot change; create a new model instead"
        )
    current = db.get(DomainModelRevision, dm.current_revision_id) if dm.current_revision_id else None
    if body.base_revision_id and current and body.base_revision_id != current.id:
        raise ApiError(
            409,
            "stale_revision",
            "The model changed since you loaded it",
            {"current_revision_id": current.id},
        )
    validation = validate_envelope(env)
    if current is not None and current.status == "draft":
        rev = update_model_draft(current, env, validation)
        rev.message = body.message or rev.message
    else:
        rev = create_model_revision(
            db, dm, env, user.id, validation, message=body.message, parent_id=current.id if current else None
        )
    audit(
        db,
        action="model.revised",
        object_type="domain_model_revision",
        object_id=rev.id,
        actor_id=user.id,
        project_id=dm.project_id,
        details={"number": rev.number},
    )
    return rev


@router.post("/model-revisions/{revision_id}/publish", response_model=ModelRevisionOut)
def publish_model_revision(revision_id: str, user: CurrentUser, db: DB):
    rev = db.get(DomainModelRevision, revision_id)
    if rev is None:
        raise ApiError(404, "not_found", "Revision not found")
    dm = _model(db, user, rev.domain_model_id, Action.edit)
    publish(db, rev)
    audit(
        db,
        action="model.published",
        object_type="domain_model_revision",
        object_id=rev.id,
        actor_id=user.id,
        project_id=dm.project_id,
    )
    return rev


@router.post("/models/validate")
def validate_model(body: dict, user: CurrentUser):
    env = DomainModelEnvelope.model_validate(body)
    return validate_envelope(env)


def merged_report(validation: dict, engine_id: str) -> ValidationReport:
    return ValidationReport.model_validate(
        validation.get("by_engine", {}).get(engine_id, {"ok": True, "issues": []})
    )
