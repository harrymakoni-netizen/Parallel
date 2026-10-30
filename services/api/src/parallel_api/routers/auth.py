from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from parallel_core.models import Organisation, OrganisationMembership, User
from parallel_core.security import issue_token, verify_password
from parallel_core.settings import get_settings

from ..deps import DB, CurrentUser
from ..errors import ApiError
from ..open_access import is_guest
from ..schemas import AuthConfigOut, LoginIn, MeOut, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config", response_model=AuthConfigOut)
def auth_config() -> AuthConfigOut:
    s = get_settings()
    return AuthConfigOut(
        mode=s.auth_mode, oidc_issuer=s.oidc_issuer, oidc_audience=s.oidc_audience, open_access=s.open_access
    )


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: DB) -> TokenOut:
    if get_settings().auth_mode != "local":
        raise ApiError(400, "oidc_enabled", "Sign in through the configured identity provider")
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect")
    return TokenOut(access_token=issue_token(user.id))


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser, db: DB) -> MeOut:
    rows = db.execute(
        select(Organisation, OrganisationMembership.role)
        .join(OrganisationMembership, OrganisationMembership.organisation_id == Organisation.id)
        .where(OrganisationMembership.user_id == user.id)
        .order_by(Organisation.name)
    ).all()
    return MeOut(
        user=UserOut.model_validate(user),
        organisations=[{"id": o.id, "name": o.name, "slug": o.slug, "role": r} for o, r in rows],
        guest=is_guest(user),
    )
