"""Bounded optimisation studies (/api/v1/optimisations)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from parallel_contracts.scenario import ScenarioContent
from parallel_core.access import Action, require_project, require_scoped, visible_case_ids
from parallel_core.audit import record as audit
from parallel_core.jobs import TASK_OPTIMISATION, celery_app
from parallel_core.models import OptimisationStudy, Scenario, ScenarioRevision
from parallel_core.optimisation import check_definition, grid_size
from parallel_core.revisions import create_scenario_revision
from parallel_core.settings import get_settings

from ..deps import DB, CurrentUser
from ..errors import ApiError
from ..schemas import OptimisationIn, OptimisationOut, ScenarioRevisionOut

router = APIRouter(tags=["optimisations"])

PRESENTATION = (
    "The best tested candidate is the best of the candidates that were actually simulated within the budget. "
    "Untested combinations were not evaluated, so no claim of a global optimum is made. Compare the recorded "
    "metrics of each candidate to see the trade-offs."
)


class AdoptIn(BaseModel):
    candidate_index: int = Field(ge=0)
    scenario_name: str = Field(min_length=2, max_length=200)


def _out(s: OptimisationStudy) -> dict:
    out = OptimisationOut.model_validate(s).model_dump(mode="json")
    out["presentation_note"] = PRESENTATION
    out["best_label"] = "Best tested candidate" if s.best_candidate_index is not None else None
    return out


def enqueue(db, study: OptimisationStudy) -> None:
    db.commit()
    if get_settings().task_always_eager:
        from parallel_core.optimisation import run_study

        run_study(study.id)
        db.refresh(study)
        return
    res = celery_app.send_task(TASK_OPTIMISATION, args=[study.id])
    study.celery_task_id = res.id


@router.post("/projects/{project_id}/optimisations", status_code=201)
def create(project_id: str, body: OptimisationIn, user: CurrentUser, db: DB):
    require_project(db, user, project_id)
    rev = db.get(ScenarioRevision, body.base_scenario_revision_id)
    sc = db.get(Scenario, rev.scenario_id) if rev else None
    if sc is None or sc.project_id != project_id:
        raise ApiError(404, "not_found", "Base scenario revision not found")
    require_scoped(db, user, project_id, sc.case_id, Action.edit)
    definition = body.model_dump(exclude={"name", "base_scenario_revision_id"})
    problems = check_definition(rev.content, definition)
    if problems:
        raise ApiError(
            422, "invalid_study", "The study definition cannot be evaluated", {"problems": problems}
        )
    definition["grid_size"] = grid_size(definition)
    s = OptimisationStudy(
        project_id=project_id,
        name=body.name,
        base_scenario_revision_id=rev.id,
        definition=definition,
        status="queued",
        created_by=user.id,
    )
    db.add(s)
    db.flush()
    audit(
        db,
        action="optimisation.created",
        object_type="optimisation_study",
        object_id=s.id,
        actor_id=user.id,
        project_id=project_id,
        details={
            "method": body.method,
            "budget": body.budget,
            **({"case_id": sc.case_id} if sc.case_id else {}),
        },
    )
    enqueue(db, s)
    return _out(s)


def _study(db, user, study_id: str, action=Action.view) -> OptimisationStudy:
    s = db.get(OptimisationStudy, study_id)
    if s is None:
        raise ApiError(404, "not_found", "Study not found")
    rev = db.get(ScenarioRevision, s.base_scenario_revision_id)
    sc = db.get(Scenario, rev.scenario_id)  # type: ignore[union-attr]
    require_scoped(db, user, s.project_id, sc.case_id, action)  # type: ignore[union-attr]
    return s


@router.get("/projects/{project_id}/optimisations")
def list_(project_id: str, user: CurrentUser, db: DB):
    require_project(db, user, project_id)
    cases = set(visible_case_ids(db, user, project_id))
    rows = db.execute(
        select(OptimisationStudy, Scenario.case_id)
        .join(ScenarioRevision, ScenarioRevision.id == OptimisationStudy.base_scenario_revision_id)
        .join(Scenario, Scenario.id == ScenarioRevision.scenario_id)
        .where(OptimisationStudy.project_id == project_id)
        .order_by(OptimisationStudy.created_at.desc())
    ).all()
    return [_out(s) for s, case_id in rows if case_id is None or case_id in cases]


@router.get("/optimisations/{study_id}")
def get(study_id: str, user: CurrentUser, db: DB):
    return _out(_study(db, user, study_id))


@router.post("/optimisations/{study_id}/cancel")
def cancel(study_id: str, user: CurrentUser, db: DB):
    s = _study(db, user, study_id, Action.edit)
    if s.status == "queued":
        s.status = "cancelled"
    elif s.status == "running":
        s.status = "cancelling"  # the study stops before its next candidate
    audit(
        db,
        action="optimisation.cancel_requested",
        object_type="optimisation_study",
        object_id=s.id,
        actor_id=user.id,
        project_id=s.project_id,
    )
    return _out(s)


@router.post("/optimisations/{study_id}/adopt", response_model=ScenarioRevisionOut, status_code=201)
def adopt(study_id: str, body: AdoptIn, user: CurrentUser, db: DB):
    """Turn a tested candidate into a new scenario that a user has explicitly adopted."""
    s = _study(db, user, study_id, Action.edit)
    if body.candidate_index >= len(s.candidates):
        raise ApiError(404, "not_found", "Candidate not found")
    cand = s.candidates[body.candidate_index]
    if cand.get("run_state") != "completed":
        raise ApiError(422, "candidate_not_tested", "Only candidates with a completed run can be adopted")
    rev = db.get(ScenarioRevision, cand["scenario_revision_id"])
    base = db.get(Scenario, db.get(ScenarioRevision, s.base_scenario_revision_id).scenario_id)  # type: ignore[union-attr]
    sc = Scenario(
        project_id=s.project_id,
        case_id=base.case_id,
        domain_model_id=base.domain_model_id,  # type: ignore[union-attr]
        name=body.scenario_name,
        engine_id=base.engine_id,
        is_baseline=False,
    )  # type: ignore[union-attr]
    db.add(sc)
    db.flush()
    new = create_scenario_revision(
        db,
        sc,
        ScenarioContent.model_validate(rev.content),
        user.id,
        origin="manual",  # type: ignore[union-attr]
        message=f"Adopted from optimisation '{s.name}' candidate {body.candidate_index} (run {cand['run_id']})",
    )
    audit(
        db,
        action="optimisation.candidate_adopted",
        object_type="scenario",
        object_id=sc.id,
        actor_id=user.id,
        project_id=s.project_id,
        details={"study_id": s.id, "candidate_index": body.candidate_index},
    )
    return new
