from __future__ import annotations

from fastapi import APIRouter
from geoalchemy2.elements import WKTElement
from geoalchemy2.shape import to_shape
from sqlalchemy import func, select

from parallel_core.access import Action, org_role, project_role, require_project
from parallel_core.audit import record as audit
from parallel_core.db import utcnow
from parallel_core.models import (
    AuditEvent,
    CaptureBatch,
    DomainModel,
    OrganisationMembership,
    Project,
    ProjectMembership,
    Report,
    Scenario,
    Scene,
    SimulationRun,
    SourceAsset,
    User,
)
from parallel_scene.georef import GeoReferenceError, check_projected

from ..deps import DB, CurrentUser, Paging
from ..errors import ApiError
from ..schemas import MemberOut, PageOut, ProjectIn, ProjectOut, ProjectPatch, UserOut

router = APIRouter(prefix="/projects", tags=["projects"])

TEMPLATES = {
    "mine_evacuation": "Mine Evacuation (JuPedSim)",
    "crashlens": "CrashLens accident scene and investigation",
    "construction": "Construction Sites (SimPy, JuPedSim, OpenFOAM)",
    "urban_planning": "Urban Planning (SUMO, JuPedSim)",
    "urban_drainage": "Urban Drainage (SWMM)",
}


def project_out(db, p: Project, user) -> ProjectOut:
    lonlat = None
    if p.location is not None:
        pt = to_shape(p.location)
        lonlat = [pt.x, pt.y]
    return ProjectOut(
        id=p.id,
        organisation_id=p.organisation_id,
        name=p.name,
        project_type=p.project_type,
        description=p.description,
        location_lonlat=lonlat,
        location_label=p.location_label,
        georeference=p.georeference,
        demo_key=p.demo_key,
        template=p.template,
        created_at=p.created_at,
        my_role=project_role(db, user, p),
    )


def _georef(body) -> dict | None:
    if body.georeference is None:
        return None
    try:
        check_projected(body.georeference.horizontal_crs)
    except GeoReferenceError as exc:
        raise ApiError(422, "invalid_georeference", str(exc)) from exc
    return body.georeference.model_dump(mode="json")


@router.get("", response_model=PageOut[ProjectOut])
def list_projects(
    user: CurrentUser, db: DB, page: Paging, organisation_id: str | None = None, demo: bool | None = None
):
    orgs = select(OrganisationMembership.organisation_id).where(OrganisationMembership.user_id == user.id)
    admin_orgs = orgs.where(OrganisationMembership.role == "admin")
    member_projects = select(ProjectMembership.project_id).where(ProjectMembership.user_id == user.id)
    q = select(Project).where(
        Project.archived_at.is_(None),
        Project.organisation_id.in_(orgs),
        (Project.organisation_id.in_(admin_orgs)) | (Project.id.in_(member_projects)),
    )
    if organisation_id:
        q = q.where(Project.organisation_id == organisation_id)
    if demo is not None:
        q = q.where(Project.demo_key.is_not(None) if demo else Project.demo_key.is_(None))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Project.created_at.desc()).limit(page.limit).offset(page.offset)).all()
    return PageOut(
        items=[project_out(db, p, user) for p in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/templates")
def templates() -> list[dict]:
    return [{"key": k, "name": v} for k, v in TEMPLATES.items()]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, user: CurrentUser, db: DB):
    role = org_role(db, user, body.organisation_id)
    if role is None:
        raise ApiError(404, "not_found", "Organisation not found")
    if body.template and body.template not in TEMPLATES:
        raise ApiError(422, "unknown_template", f"Unknown template {body.template}")
    p = Project(
        organisation_id=body.organisation_id,
        name=body.name,
        project_type=body.project_type,
        description=body.description,
        location_label=body.location_label,
        georeference=_georef(body),
        template=body.template,
        created_by=user.id,
    )
    if body.location_lonlat:
        lon, lat = body.location_lonlat
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ApiError(422, "invalid_location", "Longitude/latitude out of range")
        p.location = WKTElement(f"POINT({lon} {lat})", srid=4326)
    db.add(p)
    db.flush()
    db.add(ProjectMembership(project_id=p.id, user_id=user.id, role="editor"))
    audit(
        db,
        action="project.created",
        object_type="project",
        object_id=p.id,
        actor_id=user.id,
        organisation_id=p.organisation_id,
        project_id=p.id,
        details={"type": p.project_type, "template": p.template},
    )
    if body.template:
        from ..services.templates import apply_template

        apply_template(db, p, body.template, user.id)
    db.flush()
    return project_out(db, p, user)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, user: CurrentUser, db: DB):
    return project_out(db, require_project(db, user, project_id), user)


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(project_id: str, body: ProjectPatch, user: CurrentUser, db: DB):
    p = require_project(db, user, project_id, Action.edit)
    for k in ("name", "description", "location_label"):
        v = getattr(body, k)
        if v is not None:
            setattr(p, k, v)
    if body.georeference is not None:
        p.georeference = _georef(body)
    if body.location_lonlat:
        p.location = WKTElement(f"POINT({body.location_lonlat[0]} {body.location_lonlat[1]})", srid=4326)
    audit(
        db, action="project.updated", object_type="project", object_id=p.id, actor_id=user.id, project_id=p.id
    )
    return project_out(db, p, user)


@router.delete("/{project_id}", status_code=204)
def archive_project(project_id: str, user: CurrentUser, db: DB):
    p = require_project(db, user, project_id, Action.manage)
    p.archived_at = utcnow()
    audit(
        db,
        action="project.archived",
        object_type="project",
        object_id=p.id,
        actor_id=user.id,
        project_id=p.id,
    )


@router.get("/{project_id}/overview")
def overview(project_id: str, user: CurrentUser, db: DB):
    p = require_project(db, user, project_id)

    def count(model, col):
        return db.scalar(select(func.count()).select_from(model).where(col == p.id)) or 0

    runs = db.execute(
        select(SimulationRun.state, func.count())
        .where(SimulationRun.project_id == p.id)
        .group_by(SimulationRun.state)
    ).all()
    scenes = db.scalars(select(Scene).where(Scene.project_id == p.id)).all()
    return {
        "project": project_out(db, p, user).model_dump(mode="json"),
        "counts": {
            "capture_batches": count(CaptureBatch, CaptureBatch.project_id),
            "assets": count(SourceAsset, SourceAsset.project_id),
            "scenes": len(scenes),
            "models": count(DomainModel, DomainModel.project_id),
            "scenarios": count(Scenario, Scenario.project_id),
            "reports": count(Report, Report.project_id),
        },
        "runs_by_state": {s: n for s, n in runs},
        "scenes": [
            {"id": s.id, "name": s.name, "current_revision_id": s.current_revision_id} for s in scenes
        ],
    }


@router.get("/{project_id}/members", response_model=list[MemberOut])
def project_members(project_id: str, user: CurrentUser, db: DB):
    require_project(db, user, project_id)
    rows = db.execute(
        select(User, ProjectMembership.role)
        .join(ProjectMembership)
        .where(ProjectMembership.project_id == project_id)
    ).all()
    return [MemberOut(user=UserOut.model_validate(u), role=r) for u, r in rows]


@router.post("/{project_id}/members", response_model=MemberOut, status_code=201)
def set_project_member(project_id: str, body: dict, user: CurrentUser, db: DB):
    p = require_project(db, user, project_id, Action.manage)
    role = body.get("role")
    if role not in ("editor", "reviewer", "viewer"):
        raise ApiError(422, "invalid_role", "Project role must be editor, reviewer or viewer")
    target = db.get(User, body.get("user_id", ""))
    if target is None or org_role(db, target, p.organisation_id) is None:
        raise ApiError(422, "not_member", "User must belong to the project's organisation")
    m = db.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == p.id, ProjectMembership.user_id == target.id
        )
    )
    if m:
        m.role = role
    else:
        db.add(ProjectMembership(project_id=p.id, user_id=target.id, role=role))
    audit(
        db,
        action="project.member_set",
        object_type="user",
        object_id=target.id,
        actor_id=user.id,
        project_id=p.id,
        details={"role": role},
    )
    return MemberOut(user=UserOut.model_validate(target), role=role)


@router.get("/{project_id}/activity")
def activity(project_id: str, user: CurrentUser, db: DB, page: Paging):
    require_project(db, user, project_id)
    q = (
        select(AuditEvent, User.display_name)
        .outerjoin(User, User.id == AuditEvent.actor_id)
        .where(AuditEvent.project_id == project_id)
    )
    total = (
        db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.project_id == project_id))
        or 0
    )
    rows = db.execute(q.order_by(AuditEvent.created_at.desc()).limit(page.limit).offset(page.offset)).all()
    return {
        "items": [
            {
                "id": e.id,
                "action": e.action,
                "object_type": e.object_type,
                "object_id": e.object_id,
                "actor": name,
                "details": e.details,
                "created_at": e.created_at,
            }
            for e, name in rows
        ],
        "total": total,
        "limit": page.limit,
        "offset": page.offset,
    }
