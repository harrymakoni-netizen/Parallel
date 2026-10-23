"""Operator commands for a deployment: create the first administrator and reset a password.

    python -m parallel_api.admin create-admin --email ops@example.org --name "Operations" \
        --organisation "Example Engineering" --slug example-engineering
    python -m parallel_api.admin set-password --email ops@example.org

The password is read from PARALLEL_BOOTSTRAP_PASSWORD when set (for unattended setup), otherwise
prompted for without echo. It is never accepted on the command line, where it would reach shell
history and process listings. Further users are added by an administrator in Organisation Settings.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys

from sqlalchemy import select

from parallel_core.audit import record as audit
from parallel_core.db import session_scope
from parallel_core.models import Organisation, OrganisationMembership, User
from parallel_core.security import hash_password

from .routers.organisations import DEFAULT_SETTINGS

MIN_PASSWORD = 12


def _password(confirm: bool = True) -> str:
    pw = os.environ.get("PARALLEL_BOOTSTRAP_PASSWORD")
    if pw is None:
        pw = getpass.getpass("Password: ")
        if confirm and getpass.getpass("Repeat password: ") != pw:
            raise SystemExit("Passwords do not match.")
    if len(pw) < MIN_PASSWORD:
        raise SystemExit(f"Passwords must have at least {MIN_PASSWORD} characters.")
    return pw


def create_admin(email: str, name: str, organisation: str, slug: str | None) -> dict:
    email = email.strip().lower()
    slug = slug or re.sub(r"[^a-z0-9]+", "-", organisation.lower()).strip("-")[:80]
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        created_user = user is None
        if user is None:
            user = User(email=email, display_name=name or email.split("@")[0], password_hash=hash_password(_password()))
            db.add(user)
            db.flush()
        org = db.scalar(select(Organisation).where(Organisation.slug == slug))
        created_org = org is None
        if org is None:
            org = Organisation(name=organisation, slug=slug, settings=dict(DEFAULT_SETTINGS))
            db.add(org)
            db.flush()
            audit(db, action="organisation.created", object_type="organisation", object_id=org.id,
                  actor_id=user.id, organisation_id=org.id, details={"via": "admin command"})
        m = db.scalar(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == org.id, OrganisationMembership.user_id == user.id
            )
        )
        if m is None:
            db.add(OrganisationMembership(organisation_id=org.id, user_id=user.id, role="admin"))
        else:
            m.role = "admin"
        audit(db, action="organisation.member_added", object_type="user", object_id=user.id,
              actor_id=None, organisation_id=org.id, details={"role": "admin", "via": "admin command"})
        return {"user_id": user.id, "user_created": created_user, "organisation_id": org.id,
                "organisation_created": created_org, "slug": slug}


def set_password(email: str) -> dict:
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            raise SystemExit(f"No user with email {email}.")
        user.password_hash = hash_password(_password())
        audit(db, action="user.password_reset", object_type="user", object_id=user.id, actor_id=None,
              details={"via": "admin command"})
        return {"user_id": user.id}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m parallel_api.admin", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin", help="create (or promote) an organisation administrator")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="")
    c.add_argument("--organisation", required=True)
    c.add_argument("--slug")
    s = sub.add_parser("set-password", help="set a local user's password")
    s.add_argument("--email", required=True)
    a = p.parse_args(argv)
    out = create_admin(a.email, a.name, a.organisation, a.slug) if a.cmd == "create-admin" else set_password(a.email)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
