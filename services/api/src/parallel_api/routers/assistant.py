"""Scoped AI assistant (/api/v1/assistant). Optional: Parallel works fully without it."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from parallel_core.access import Action, require_project, require_scoped
from parallel_core.audit import record as audit
from parallel_core.db import utcnow
from parallel_core.models import AssistantSession, AssistantToolCall, Scenario, ScenarioRevision
from parallel_core.revisions import adopt

from ..assistant import service
from ..assistant.providers import PROMPT_TEMPLATE_VERSION, get_provider
from ..deps import DB, CurrentUser
from ..errors import ApiError
from ..schemas import ReportIn

router = APIRouter(tags=["assistant"])


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


def _session_out(s: AssistantSession) -> dict:
    return {
        "id": s.id,
        "project_id": s.project_id,
        "provider": s.provider,
        "model": s.model,
        "prompt_template_version": s.prompt_template_version,
        "created_at": s.created_at,
    }


@router.get("/projects/{project_id}/assistant/status")
def status(project_id: str, user: CurrentUser, db: DB):
    project = require_project(db, user, project_id)
    return service.availability(db, project)


@router.post("/projects/{project_id}/assistant/sessions", status_code=201)
def create_session(project_id: str, user: CurrentUser, db: DB):
    project = require_project(db, user, project_id)
    st = service.availability(db, project)
    if not st["available"]:
        raise ApiError(503, "assistant_unavailable", st["reason"])
    p = get_provider()
    s = AssistantSession(
        project_id=project_id,
        user_id=user.id,
        provider=p.name,
        model=p.model,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
    )
    db.add(s)
    db.flush()
    audit(
        db,
        action="assistant.session_started",
        object_type="assistant_session",
        object_id=s.id,
        actor_id=user.id,
        project_id=project_id,
        details={"provider": p.name, "model": p.model},
    )
    return _session_out(s)


@router.get("/projects/{project_id}/assistant/sessions")
def list_sessions(project_id: str, user: CurrentUser, db: DB):
    require_project(db, user, project_id)
    rows = db.scalars(
        select(AssistantSession)
        .where(AssistantSession.project_id == project_id, AssistantSession.user_id == user.id)
        .order_by(AssistantSession.created_at.desc())
    ).all()
    return [_session_out(s) for s in rows]


def _session(db, user, session_id: str) -> AssistantSession:
    s = db.get(AssistantSession, session_id)
    if s is None or s.user_id != user.id:  # sessions are private to the person who started them
        raise ApiError(404, "not_found", "Session not found")
    require_project(db, user, s.project_id)
    return s


@router.get("/assistant/sessions/{session_id}")
def get_session(session_id: str, user: CurrentUser, db: DB):
    s = _session(db, user, session_id)
    return {**_session_out(s), "items": service.render(db, s)}


@router.post("/assistant/sessions/{session_id}/messages")
def send(session_id: str, body: MessageIn, user: CurrentUser, db: DB):
    s = _session(db, user, session_id)
    project = require_project(db, user, s.project_id)
    seqs = service.respond(db, s, user, project, body.text)
    return {"items": service.render(db, s, since_seq=seqs[0] if seqs else 0)}


@router.post("/assistant/tool-calls/{call_id}/adopt")
def adopt_suggestion(call_id: str, user: CurrentUser, db: DB):
    """Explicit user adoption of an assistant suggestion (scenario draft or report narrative)."""
    c = db.get(AssistantToolCall, call_id)
    s = db.get(AssistantSession, c.session_id) if c else None
    if c is None or s is None or s.user_id != user.id:
        raise ApiError(404, "not_found", "Suggestion not found")
    if c.error or c.tool_name not in ("create_scenario_draft", "draft_report"):
        raise ApiError(422, "not_a_suggestion", "This tool call is not an adoptable suggestion")
    if c.adopted_by:
        raise ApiError(409, "already_adopted", "This suggestion has already been adopted")
    if c.tool_name == "create_scenario_draft":
        rev = db.get(ScenarioRevision, c.result["scenario_revision_id"])
        sc = db.get(Scenario, rev.scenario_id) if rev else None
        if sc is None:
            raise ApiError(404, "not_found", "Draft no longer exists")
        require_scoped(db, user, sc.project_id, sc.case_id, Action.edit)
        adopt(rev, user.id)
        obj_id, out = rev.id, {"scenario_revision_id": rev.id, "scenario_id": sc.id}
        audit(
            db,
            action="scenario.adopted",
            object_type="scenario_revision",
            object_id=rev.id,
            actor_id=user.id,
            project_id=sc.project_id,
            details={"origin": "ai", "assistant_tool_call_id": c.id},
        )
    else:
        from .reports import create as create_report

        sug = c.result["suggestion"]
        rep = create_report(
            s.project_id,
            ReportIn(
                kind="project",
                title=sug["title"],
                run_ids=sug["run_ids"],
                narrative=sug["narrative"],
                narrative_origin="ai_draft_adopted",
            ),
            user,
            db,
        )
        obj_id, out = rep.id, {"report_id": rep.id, "status": rep.status}
    c.adopted_by, c.adopted_at, c.adopted_object_id = user.id, utcnow(), obj_id
    return {"adopted": True, **out}
