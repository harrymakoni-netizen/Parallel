"""Scoped review records (spec section 5): a review states exactly what was reviewed."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from parallel_core.access import Action, require_case, require_project, require_scoped
from parallel_core.audit import record as audit
from parallel_core.models import (
    DomainModel,
    DomainModelRevision,
    EvidenceObservation,
    Hypothesis,
    Report,
    ReviewRecord,
    Scenario,
    ScenarioRevision,
    Scene,
    SceneRevision,
    SimulationRun,
)

from ..deps import DB, CurrentUser
from ..errors import ApiError
from ..schemas import ReviewIn, ReviewOut

router = APIRouter(tags=["reviews"])

SCOPE_NOTE = {
    "geometry": "Geometry only (measured/prepared shapes and their alignment).",
    "evidence_annotations": "Evidence annotations only (observations and their spatial references).",
    "model_assumptions": "Model assumptions only (inputs, parameters and their provenance).",
    "interpretation": "Interpretation only (analyst reasoning about results or evidence).",
    "report": "Report content only.",
}


def _project_for(db, user, body: ReviewIn) -> tuple[str, str | None]:
    """(project id, case id) of the reviewed object, after checking the reviewer's role."""
    t, oid = body.object_type, body.object_id
    if t in ("evidence_observation", "hypothesis"):
        obj = db.get(EvidenceObservation if t == "evidence_observation" else Hypothesis, oid)
        if obj is None:
            raise ApiError(404, "not_found", "Object not found")
        case = require_case(db, user, obj.case_id, Action.review)
        return case.project_id, case.id
    if t == "scene_revision":
        rev = db.get(SceneRevision, oid)
        parent = db.get(Scene, rev.scene_id) if rev else None
    elif t == "domain_model_revision":
        rev = db.get(DomainModelRevision, oid)
        parent = db.get(DomainModel, rev.domain_model_id) if rev else None
    elif t == "scenario_revision":
        rev = db.get(ScenarioRevision, oid)
        parent = db.get(Scenario, rev.scenario_id) if rev else None
    elif t == "simulation_run":
        parent = db.get(SimulationRun, oid)
    else:
        parent = db.get(Report, oid)
    if parent is None:
        raise ApiError(404, "not_found", "Object not found")
    case_id = getattr(parent, "case_id", None)
    require_scoped(db, user, parent.project_id, case_id, Action.review)
    return parent.project_id, case_id


@router.post("/reviews", response_model=ReviewOut, status_code=201)
def create(body: ReviewIn, user: CurrentUser, db: DB):
    project_id, case_id = _project_for(db, user, body)
    r = ReviewRecord(
        project_id=project_id,
        object_type=body.object_type,
        object_id=body.object_id,
        scope=body.scope,
        status=body.status,
        comment=body.comment,
        reviewer_id=user.id,
    )
    db.add(r)
    if body.object_type == "evidence_observation":
        obs = db.get(EvidenceObservation, body.object_id)
        obs.review_status = (
            "reviewed"
            if body.status == "approved"
            else "disputed"
            if body.status == "changes_requested"
            else obs.review_status
        )  # type: ignore[union-attr]
    if body.object_type == "report":
        rep = db.get(Report, body.object_id)
        rep.review_status = f"{body.status} ({body.scope})"  # type: ignore[union-attr]
    db.flush()
    audit(
        db,
        action="review.recorded",
        object_type=body.object_type,
        object_id=body.object_id,
        actor_id=user.id,
        project_id=project_id,
        details={"scope": body.scope, "status": body.status, **({"case_id": case_id} if case_id else {})},
    )
    return r


@router.get("/projects/{project_id}/reviews")
def list_(project_id: str, user: CurrentUser, db: DB, object_id: str | None = None):
    require_project(db, user, project_id)
    q = select(ReviewRecord).where(
        ReviewRecord.project_id == project_id,
        ReviewRecord.object_type.not_in(["evidence_observation", "hypothesis"]),
    )
    if object_id:
        q = q.where(ReviewRecord.object_id == object_id)
    rows = db.scalars(q.order_by(ReviewRecord.created_at.desc())).all()
    return [
        {**ReviewOut.model_validate(r).model_dump(mode="json"), "scope_note": SCOPE_NOTE[r.scope]}
        for r in rows
    ]
