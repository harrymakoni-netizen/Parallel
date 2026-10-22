"""Audit events for consequential changes (never containing secrets or evidence content)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent, DomainModel, DomainModelRevision, Scenario, ScenarioRevision, SimulationRun

_SCOPED = {"domain_model": DomainModel, "scenario": Scenario, "simulation_run": SimulationRun}
_REVISIONS = {
    "domain_model_revision": (DomainModelRevision, "domain_model_id", DomainModel),
    "scenario_revision": (ScenarioRevision, "scenario_id", Scenario),
}


def case_of(db: Session, object_type: str, object_id: str | None) -> str | None:
    """The CrashLens case an object belongs to, if any."""
    if object_id is None:
        return None
    if object_type in _SCOPED:
        obj = db.get(_SCOPED[object_type], object_id)
        return getattr(obj, "case_id", None)
    if object_type in _REVISIONS:
        rev_model, parent_attr, parent_model = _REVISIONS[object_type]
        rev = db.get(rev_model, object_id)
        parent = db.get(parent_model, getattr(rev, parent_attr)) if rev is not None else None
        return getattr(parent, "case_id", None)
    return None


def record(
    db: Session,
    *,
    action: str,
    object_type: str,
    object_id: str | None,
    actor_id: str | None,
    organisation_id: str | None = None,
    project_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    details = dict(details or {})
    if "case_id" not in details and (case_id := case_of(db, object_type, object_id)):
        details["case_id"] = case_id
    ev = AuditEvent(
        action=action,
        object_type=object_type,
        object_id=object_id,
        actor_id=actor_id,
        organisation_id=organisation_id,
        project_id=project_id,
        details=details,
    )
    db.add(ev)
    return ev
