"""Open access mode: no sign-in, a limited shared guest.

When ``PARALLEL_OPEN_ACCESS`` is on, a request without a token is served as one guest user. The guest is an
ordinary user, so every authorisation rule still applies to it: it belongs to a sandbox organisation as a plain
member (it can create projects there, upload within limits, and run scenarios), and optionally has read-only
access to the demonstration projects. It is not an administrator, cannot create organisations, cannot reach any
other organisation's projects, and the sandbox has external AI switched off. Everything in the sandbox is shared
by every visitor. A request that carries a token is authorised as that token's user, as always.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from parallel_core.db import session_scope
from parallel_core.models import (
    Organisation,
    OrganisationMembership,
    Project,
    ReconstructionJob,
    SourceAsset,
    User,
)
from parallel_core.settings import get_settings

from .errors import ApiError

GUEST_ORG_NAME, GUEST_ORG_SLUG = "Open access", "open-access"
GUEST_ORG_SETTINGS = {"allow_external_ai": False, "allow_case_media_to_ai": False}


def is_guest(user: User) -> bool:
    s = get_settings()
    return s.open_access and user.email == s.open_access_email


def ensure_guest(db: Session) -> User:
    """The guest user and its sandbox organisation, created on first use. Idempotent."""
    s = get_settings()
    user = db.scalar(select(User).where(User.email == s.open_access_email))
    if user is None:
        user = User(email=s.open_access_email, display_name="Guest", password_hash=None)  # no password: cannot sign in
        db.add(user)
        db.flush()
    org = db.scalar(select(Organisation).where(Organisation.slug == GUEST_ORG_SLUG))
    if org is None:
        org = Organisation(name=GUEST_ORG_NAME, slug=GUEST_ORG_SLUG, settings=dict(GUEST_ORG_SETTINGS))
        db.add(org)
        db.flush()
    if not db.scalar(
        select(OrganisationMembership.id).where(
            OrganisationMembership.user_id == user.id, OrganisationMembership.organisation_id == org.id
        )
    ):
        db.add(OrganisationMembership(organisation_id=org.id, user_id=user.id, role="member"))
        db.flush()
    return user


def guest_user(db: Session) -> User:
    """The guest, provisioning it if the API started before the mode was switched on."""
    s = get_settings()
    user = db.scalar(select(User).where(User.email == s.open_access_email))
    if user is not None and user.is_active:
        return user
    try:
        with session_scope() as other:
            ensure_guest(other)
    except IntegrityError:  # another request provisioned it first
        pass
    user = db.scalar(select(User).where(User.email == s.open_access_email))
    if user is None or not user.is_active:
        raise ApiError(503, "open_access_unavailable", "Open access is on but the guest account could not be created")
    return user


def upload_limit(user: User) -> int:
    s = get_settings()
    return min(s.max_upload_bytes, s.open_access_max_upload_bytes) if is_guest(user) else s.max_upload_bytes


def guard_storage(db: Session, user: User) -> None:
    """A guest may not grow the shared sandbox past its storage cap."""
    if not is_guest(user):
        return
    used = db.scalar(
        select(func.coalesce(func.sum(SourceAsset.size_bytes), 0))
        .join(Project, Project.id == SourceAsset.project_id)
        .join(Organisation, Organisation.id == Project.organisation_id)
        .where(Organisation.slug == GUEST_ORG_SLUG)
    )
    cap = get_settings().open_access_storage_cap_bytes
    if used >= cap:
        raise ApiError(
            413, "open_access_storage_full",
            "The shared open-access sandbox is full. Sign in with an account to continue, or ask the owner to clear it.")


def guard_reconstructions(db: Session, user: User) -> None:
    """At most a few reconstructions may be queued or running for guests at once (they are CPU heavy)."""
    if not is_guest(user):
        return
    active = db.scalar(
        select(func.count(ReconstructionJob.id))
        .join(Project, Project.id == ReconstructionJob.project_id)
        .join(Organisation, Organisation.id == Project.organisation_id)
        .where(Organisation.slug == GUEST_ORG_SLUG, ReconstructionJob.status.in_(("queued", "running", "resuming")))
    )
    if active >= get_settings().open_access_max_active_reconstructions:
        raise ApiError(429, "open_access_busy", "Reconstructions from open access are busy; try again in a few minutes")
