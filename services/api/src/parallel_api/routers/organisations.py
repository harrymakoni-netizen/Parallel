from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from parallel_contracts.engines import ExecutionMode
from parallel_core.access import org_role
from parallel_core.audit import record as audit
from parallel_core.db import utcnow
from parallel_core.models import EngineInstallation, Organisation, OrganisationMembership, User
from parallel_core.runs import check_engine
from parallel_core.security import hash_password
from parallel_engines.registry import get_adapter

from ..deps import DB, CurrentUser
from ..errors import ApiError
from ..open_access import is_guest
from ..schemas import MemberIn, MemberOut, OrganisationIn, OrganisationOut, OrgSettingsIn, UserOut

router = APIRouter(prefix="/organisations", tags=["organisations"])

DEFAULT_SETTINGS = {"allow_external_ai": True, "allow_case_media_to_ai": False}


def _require_admin(db, user: User, org_id: str) -> Organisation:
    org = db.get(Organisation, org_id)
    role = org_role(db, user, org_id) if org else None
    if org is None or role is None:
        raise ApiError(404, "not_found", "Organisation not found")
    if role != "admin":
        raise ApiError(403, "forbidden", "Organisation administrator role required")
    return org


@router.get("", response_model=list[OrganisationOut])
def list_orgs(user: CurrentUser, db: DB):
    return db.scalars(
        select(Organisation).join(OrganisationMembership).where(OrganisationMembership.user_id == user.id)
    ).all()


@router.post("", response_model=OrganisationOut, status_code=201)
def create_org(body: OrganisationIn, user: CurrentUser, db: DB):
    if is_guest(user):
        raise ApiError(403, "forbidden", "Sign in to create an organisation")
    if db.scalar(select(Organisation).where(Organisation.slug == body.slug)):
        raise ApiError(409, "slug_taken", "Organisation slug already in use")
    org = Organisation(name=body.name, slug=body.slug, settings=dict(DEFAULT_SETTINGS))
    db.add(org)
    db.flush()
    db.add(OrganisationMembership(organisation_id=org.id, user_id=user.id, role="admin"))
    audit(
        db,
        action="organisation.created",
        object_type="organisation",
        object_id=org.id,
        actor_id=user.id,
        organisation_id=org.id,
    )
    return org


@router.patch("/{org_id}/settings", response_model=OrganisationOut)
def update_settings(org_id: str, body: OrgSettingsIn, user: CurrentUser, db: DB):
    org = _require_admin(db, user, org_id)
    org.settings = {**(org.settings or {}), **body.model_dump(exclude_none=True)}
    audit(
        db,
        action="organisation.settings_changed",
        object_type="organisation",
        object_id=org.id,
        actor_id=user.id,
        organisation_id=org.id,
        details=body.model_dump(exclude_none=True),
    )
    return org


@router.get("/{org_id}/members", response_model=list[MemberOut])
def members(org_id: str, user: CurrentUser, db: DB):
    if org_role(db, user, org_id) is None:
        raise ApiError(404, "not_found", "Organisation not found")
    rows = db.execute(
        select(User, OrganisationMembership.role)
        .join(OrganisationMembership)
        .where(OrganisationMembership.organisation_id == org_id)
        .order_by(User.display_name)
    ).all()
    return [MemberOut(user=UserOut.model_validate(u), role=r) for u, r in rows]


@router.post("/{org_id}/members", response_model=MemberOut, status_code=201)
def add_member(org_id: str, body: MemberIn, user: CurrentUser, db: DB):
    _require_admin(db, user, org_id)
    if body.role not in ("admin", "member"):
        raise ApiError(422, "invalid_role", "Organisation role must be admin or member")
    target = db.scalar(select(User).where(User.email == body.email.lower()))
    if target is None:
        if not body.password:
            raise ApiError(422, "password_required", "New local users need an initial password")
        target = User(
            email=body.email.lower(),
            display_name=body.display_name or body.email.split("@")[0],
            password_hash=hash_password(body.password),
        )
        db.add(target)
        db.flush()
    m = db.scalar(
        select(OrganisationMembership).where(
            OrganisationMembership.organisation_id == org_id, OrganisationMembership.user_id == target.id
        )
    )
    if m:
        m.role = body.role
    else:
        db.add(OrganisationMembership(organisation_id=org_id, user_id=target.id, role=body.role))
    audit(
        db,
        action="organisation.member_set",
        object_type="user",
        object_id=target.id,
        actor_id=user.id,
        organisation_id=org_id,
        details={"role": body.role},
    )
    return MemberOut(user=UserOut.model_validate(target), role=body.role)


@router.delete("/{org_id}/members/{user_id}", status_code=204)
def remove_member(org_id: str, user_id: str, user: CurrentUser, db: DB):
    _require_admin(db, user, org_id)
    if user_id == user.id:
        raise ApiError(409, "self_removal", "Administrators cannot remove themselves")
    db.query(OrganisationMembership).filter(
        OrganisationMembership.organisation_id == org_id, OrganisationMembership.user_id == user_id
    ).delete()
    audit(
        db,
        action="organisation.member_removed",
        object_type="user",
        object_id=user_id,
        actor_id=user.id,
        organisation_id=org_id,
    )


# ---------------------------------------------------------------- engine installations (integrations)
@router.get("/{org_id}/engine-installations")
def list_installations(org_id: str, user: CurrentUser, db: DB):
    if org_role(db, user, org_id) is None:
        raise ApiError(404, "not_found", "Organisation not found")
    rows = db.scalars(select(EngineInstallation).where(EngineInstallation.organisation_id == org_id)).all()
    return [_inst(r) for r in rows]


@router.post("/{org_id}/engine-installations", status_code=201)
def register_installation(org_id: str, body: dict, user: CurrentUser, db: DB):
    """Register a licensed external installation (file exchange) so its availability can become Ready."""
    _require_admin(db, user, org_id)
    engine_id = body.get("engine_id", "")
    adapter = get_adapter(engine_id)
    inst = EngineInstallation(
        organisation_id=org_id,
        engine_id=engine_id,
        execution_mode=body.get("execution_mode", "file_exchange"),
        label=body.get("label") or f"{adapter.capability.name} installation",
        edition=body.get("edition"),
        version=body.get("version"),
        licence_confirmed=bool(body.get("licence_confirmed")),
        licence_holder=body.get("licence_holder"),
        operator_contact=body.get("operator_contact"),
        created_by=user.id,
    )
    db.add(inst)
    db.flush()
    check = check_engine(db, org_id, engine_id, ExecutionMode(inst.execution_mode))
    inst.last_check, inst.last_checked_at = check.model_dump(mode="json"), utcnow()
    audit(
        db,
        action="engine.installation_registered",
        object_type="engine_installation",
        object_id=inst.id,
        actor_id=user.id,
        organisation_id=org_id,
        details={"engine_id": engine_id, "licence_confirmed": inst.licence_confirmed},
    )
    return _inst(inst)


def _inst(r: EngineInstallation) -> dict:
    return {
        "id": r.id,
        "engine_id": r.engine_id,
        "execution_mode": r.execution_mode,
        "label": r.label,
        "edition": r.edition,
        "version": r.version,
        "licence_confirmed": r.licence_confirmed,
        "licence_holder": r.licence_holder,
        "operator_contact": r.operator_contact,
        "last_check": r.last_check,
        "last_checked_at": r.last_checked_at,
    }
