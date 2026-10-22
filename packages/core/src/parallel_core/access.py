"""Server-side authorisation (spec section 5).

* Organisation administrators manage membership, integrations and settings,
  and have editor-level access to their organisation's projects.
* Project roles: editor (capture, model, scenario, run), reviewer (inspect and
  record scoped reviews), viewer (read).
* CrashLens cases require explicit assignment (investigator or reviewer).
  Organisation or project access never exposes an investigation by itself.
Objects the user may not see are reported as not found (no existence leaks).
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Case, CaseAssignment, OrganisationMembership, Project, ProjectMembership, User


class Action(StrEnum):
    view = "view"
    edit = "edit"
    review = "review"
    manage = "manage"


class AccessDenied(PermissionError):
    def __init__(self, message: str = "not found", status: int = 404):
        super().__init__(message)
        self.status = status


PROJECT_RULES: dict[str, set[Action]] = {
    "org_admin": {Action.view, Action.edit, Action.review, Action.manage},
    "editor": {Action.view, Action.edit},
    "reviewer": {Action.view, Action.review},
    "viewer": {Action.view},
}

CASE_RULES: dict[str, set[Action]] = {
    "investigator": {Action.view, Action.edit},
    "reviewer": {Action.view, Action.review},
}


def org_role(db: Session, user: User, organisation_id: str) -> str | None:
    return db.scalar(
        select(OrganisationMembership.role).where(
            OrganisationMembership.user_id == user.id,
            OrganisationMembership.organisation_id == organisation_id,
        )
    )


def project_role(db: Session, user: User, project: Project) -> str | None:
    if org_role(db, user, project.organisation_id) == "admin":
        return "org_admin"
    if org_role(db, user, project.organisation_id) is None:
        return None
    return db.scalar(
        select(ProjectMembership.role).where(
            ProjectMembership.user_id == user.id, ProjectMembership.project_id == project.id
        )
    )


def can_project(db: Session, user: User, project: Project, action: Action) -> bool:
    role = project_role(db, user, project)
    return role is not None and action in PROJECT_RULES.get(role, set())


def require_project(db: Session, user: User, project_id: str, action: Action = Action.view) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.archived_at is not None:
        raise AccessDenied()
    role = project_role(db, user, project)
    if role is None:
        raise AccessDenied()
    if action not in PROJECT_RULES.get(role, set()):
        raise AccessDenied(f"your project role ({role}) does not permit '{action.value}'", status=403)
    return project


def case_role(db: Session, user: User, case: Case) -> str | None:
    return db.scalar(
        select(CaseAssignment.role).where(
            CaseAssignment.case_id == case.id, CaseAssignment.user_id == user.id
        )
    )


def require_case(db: Session, user: User, case_id: str, action: Action = Action.view) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise AccessDenied()
    project = db.get(Project, case.project_id)
    if project is None or project_role(db, user, project) is None:
        raise AccessDenied()
    role = case_role(db, user, case)
    if role is None:
        raise AccessDenied()  # explicit assignment required, even for organisation administrators
    if action not in CASE_RULES.get(role, set()):
        raise AccessDenied(f"your case role ({role}) does not permit '{action.value}'", status=403)
    return case


def require_scoped(
    db: Session, user: User, project_id: str, case_id: str | None, action: Action = Action.view
) -> Project:
    """Project-level objects follow project roles; case-scoped objects follow the case assignment."""
    if case_id is None:
        return require_project(db, user, project_id, action)
    project = require_project(db, user, project_id)
    require_case(db, user, case_id, action)
    return project


def visible_case_ids(db: Session, user: User, project_id: str) -> list[str]:
    return list(
        db.scalars(
            select(Case.id)
            .join(CaseAssignment, CaseAssignment.case_id == Case.id)
            .where(Case.project_id == project_id, CaseAssignment.user_id == user.id)
        )
    )
