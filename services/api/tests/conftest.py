"""API test fixtures: a real PostgreSQL+PostGIS database (migrated), local storage and real engine execution."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from parallel_core import testing


@pytest.fixture(scope="session")
def database(tmp_path_factory):
    url = testing.create_test_database()
    testing.configure_environment(url, tmp_path_factory.mktemp("parallel"))
    yield url
    testing.reset_caches()
    if os.environ.get("PARALLEL_KEEP_TEST_DB") != "1":
        testing.drop_test_database(url)


@pytest.fixture(scope="session")
def client(database):
    from parallel_api.main import create_app

    with TestClient(create_app()) as c:
        yield c


@dataclass
class Actor:
    id: str
    email: str
    token: str

    @property
    def h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}


@pytest.fixture(scope="session")
def world(client, database):
    """An organisation with an admin, editor, reviewer, viewer and an outsider in another organisation."""
    from parallel_core.db import session_scope
    from parallel_core.models import Organisation, OrganisationMembership, User
    from parallel_core.security import hash_password, issue_token

    actors = {}
    with session_scope() as db:
        org = Organisation(
            name="Test Org",
            slug="test-org",
            settings={"allow_external_ai": True, "allow_case_media_to_ai": False},
        )
        other = Organisation(name="Other Org", slug="other-org", settings={})
        db.add_all([org, other])
        db.flush()
        for name, role, o in (
            ("admin", "admin", org),
            ("editor", "member", org),
            ("reviewer", "member", org),
            ("viewer", "member", org),
            ("investigator", "member", org),
            ("outsider", "admin", other),
        ):
            u = User(
                email=f"{name}@example.test",
                display_name=name.title(),
                password_hash=hash_password("password123"),
            )
            db.add(u)
            db.flush()
            db.add(OrganisationMembership(organisation_id=o.id, user_id=u.id, role=role))
            actors[name] = Actor(u.id, u.email, issue_token(u.id))
        actors["org_id"] = org.id
        actors["other_org_id"] = other.id
    return actors


@pytest.fixture(scope="session")
def project(client, world):
    admin, editor, reviewer, viewer = (world[k] for k in ("admin", "editor", "reviewer", "viewer"))
    r = client.post(
        "/api/v1/projects",
        headers=editor.h,
        json={
            "organisation_id": world["org_id"],
            "name": "Test mine",
            "project_type": "mine_evacuation",
            "location_lonlat": [27.9, -26.1],
            "template": "mine_evacuation",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    for who, role in ((reviewer, "reviewer"), (viewer, "viewer")):
        r = client.post(
            f"/api/v1/projects/{pid}/members", headers=admin.h, json={"user_id": who.id, "role": role}
        )
        assert r.status_code == 201, r.text
    return pid
