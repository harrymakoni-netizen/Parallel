"""Audit events for consequential changes (never containing secrets or evidence content)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent


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
    ev = AuditEvent(
        action=action,
        object_type=object_type,
        object_id=object_id,
        actor_id=actor_id,
        organisation_id=organisation_id,
        project_id=project_id,
        details=details or {},
    )
    db.add(ev)
    return ev
