"""Scenarios and their revisions (/api/v1/scenarios)."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.scenario import ScenarioContent
from parallel_core.access import Action, require_project, require_scoped, visible_case_ids
from parallel_core.audit import record as audit
from parallel_core.models import DomainModel, DomainModelRevision, Scenario, ScenarioRevision
from parallel_core.revisions import adopt, create_scenario_revision, update_scenario_draft
from parallel_engines.registry import get_adapter

from ..deps import DB, CurrentUser
from ..errors import ApiError
from ..schemas import RevisionIn, ScenarioIn, ScenarioOut, ScenarioRevisionOut

router = APIRouter(tags=["scenarios"])


def _check_engine(content: ScenarioContent) -> None:
    try:
        adapter = get_adapter(content.engine_id)
    except KeyError as exc:
        raise ApiError(422, "unknown_engine", str(exc)) from exc
    if content.discipline.value not in adapter.capability.disciplines:
        raise ApiError(
            422, "engine_discipline", f"{adapter.capability.name} does not model {content.discipline.value}"
        )


@router.get("/projects/{project_id}/scenarios", response_model=list[ScenarioOut])
def list_scenarios(project_id: str, user: CurrentUser, db: DB, domain_model_id: str | None = None):
    require_project(db, user, project_id)
    cases = visible_case_ids(db, user, project_id)
    q = select(Scenario).where(
        Scenario.project_id == project_id, Scenario.case_id.is_(None) | Scenario.case_id.in_(cases)
    )
    if domain_model_id:
        q = q.where(Scenario.domain_model_id == domain_model_id)
    return db.scalars(q.order_by(Scenario.created_at)).all()


@router.post("/projects/{project_id}/scenarios", response_model=ScenarioRevisionOut, status_code=201)
def create_scenario(project_id: str, body: ScenarioIn, user: CurrentUser, db: DB):
    require_project(db, user, project_id)
    dm = db.get(DomainModel, body.domain_model_id)
    if dm is None or dm.project_id != project_id:
        raise ApiError(404, "not_found", "Domain model not found")
    require_scoped(db, user, project_id, dm.case_id, Action.edit)
    content = ScenarioContent.model_validate(body.content)
    if content.discipline.value != dm.discipline:
        raise ApiError(422, "discipline_mismatch", "Scenario discipline must match its model")
    _check_engine(content)
    sc = Scenario(
        project_id=project_id,
        case_id=dm.case_id,
        domain_model_id=dm.id,
        name=body.name,
        engine_id=content.engine_id,
        is_baseline=content.is_baseline,
    )
    db.add(sc)
    db.flush()
    rev = create_scenario_revision(
        db, sc, content, user.id, origin="manual", message=body.message or "created"
    )
    audit(
        db,
        action="scenario.created",
        object_type="scenario",
        object_id=sc.id,
        actor_id=user.id,
        project_id=project_id,
    )
    return rev


def _scenario(db, user, scenario_id: str, action=Action.view) -> Scenario:
    sc = db.get(Scenario, scenario_id)
    if sc is None:
        raise ApiError(404, "not_found", "Scenario not found")
    require_scoped(db, user, sc.project_id, sc.case_id, action)
    return sc


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
def get_scenario(scenario_id: str, user: CurrentUser, db: DB):
    return _scenario(db, user, scenario_id)


@router.get("/scenarios/{scenario_id}/revisions", response_model=list[ScenarioRevisionOut])
def revisions(scenario_id: str, user: CurrentUser, db: DB):
    _scenario(db, user, scenario_id)
    return db.scalars(
        select(ScenarioRevision)
        .where(ScenarioRevision.scenario_id == scenario_id)
        .order_by(ScenarioRevision.number.desc())
    ).all()


@router.get("/scenario-revisions/{revision_id}", response_model=ScenarioRevisionOut)
def get_revision(revision_id: str, user: CurrentUser, db: DB):
    rev = db.get(ScenarioRevision, revision_id)
    if rev is None:
        raise ApiError(404, "not_found", "Revision not found")
    _scenario(db, user, rev.scenario_id)
    return rev


@router.post("/scenarios/{scenario_id}/revisions", response_model=ScenarioRevisionOut, status_code=201)
def save_revision(scenario_id: str, body: RevisionIn, user: CurrentUser, db: DB):
    sc = _scenario(db, user, scenario_id, Action.edit)
    content = ScenarioContent.model_validate(body.content)
    if content.engine_id != sc.engine_id:
        raise ApiError(422, "engine_change", "Create a new scenario to use a different engine")
    current = db.get(ScenarioRevision, sc.current_revision_id) if sc.current_revision_id else None
    if body.base_revision_id and current and body.base_revision_id != current.id:
        raise ApiError(
            409,
            "stale_revision",
            "The scenario changed since you loaded it",
            {"current_revision_id": current.id},
        )
    if current is not None and current.status == "draft" and current.origin == "manual":
        rev = update_scenario_draft(current, content)
        rev.message = body.message or rev.message
    else:
        rev = create_scenario_revision(
            db,
            sc,
            content,
            user.id,
            origin="manual",
            message=body.message,
            parent_id=current.id if current else None,
        )
    sc.is_baseline = content.is_baseline
    audit(
        db,
        action="scenario.revised",
        object_type="scenario_revision",
        object_id=rev.id,
        actor_id=user.id,
        project_id=sc.project_id,
        details={"number": rev.number},
    )
    return rev


@router.post("/scenario-revisions/{revision_id}/validate")
def validate_revision(revision_id: str, user: CurrentUser, db: DB):
    rev = db.get(ScenarioRevision, revision_id)
    if rev is None:
        raise ApiError(404, "not_found", "Revision not found")
    sc = _scenario(db, user, rev.scenario_id)
    dm = db.get(DomainModel, sc.domain_model_id)
    model_rev = db.get(DomainModelRevision, dm.current_revision_id)  # type: ignore[union-attr,arg-type]
    model = DomainModelEnvelope.model_validate(model_rev.content)  # type: ignore[union-attr]
    content = ScenarioContent.model_validate(rev.content)
    adapter = get_adapter(sc.engine_id)
    report = adapter.validate_scenario(model, content)
    estimate = adapter.estimate_resources(model, content) if report.ok else None
    return {
        "validation": report.model_dump(mode="json"),
        "resource_estimate": estimate.model_dump(mode="json") if estimate else None,
        "model_revision_id": model_rev.id,
    }  # type: ignore[union-attr]


@router.post("/scenario-revisions/{revision_id}/adopt", response_model=ScenarioRevisionOut)
def adopt_revision(revision_id: str, user: CurrentUser, db: DB):
    """Explicit user action: adopt an assistant-drafted scenario as a model input."""
    rev = db.get(ScenarioRevision, revision_id)
    if rev is None:
        raise ApiError(404, "not_found", "Revision not found")
    sc = _scenario(db, user, rev.scenario_id, Action.edit)
    adopt(rev, user.id)
    audit(
        db,
        action="scenario.adopted",
        object_type="scenario_revision",
        object_id=rev.id,
        actor_id=user.id,
        project_id=sc.project_id,
        details={"origin": rev.origin},
    )
    return rev
