"""Open access mode: no sign-in, a limited shared guest. It must work without a token and stay inside its
sandbox: no other organisation's data, no administration, bounded uploads and reconstructions, no AI spend."""

from __future__ import annotations

import pytest

from parallel_core.settings import get_settings

GUEST = "guest@parallel-open.example"


@pytest.fixture
def open_access(monkeypatch):
    def turn_on(**env):
        monkeypatch.setenv("PARALLEL_OPEN_ACCESS", "true")
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        get_settings.cache_clear()

    yield turn_on
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def project(client, world):
    return client.post("/api/v1/projects", headers=world["editor"].h, json={
        "organisation_id": world["org_id"], "name": "Private to the team", "project_type": "general"}).json()["id"]


def me(client):
    return client.get("/api/v1/auth/me").json()


def sandbox_id(client) -> str:
    return next(o["id"] for o in me(client)["organisations"] if o["slug"] == "open-access")


def test_off_by_default_every_request_needs_a_token(client):
    assert client.get("/api/v1/auth/config").json()["open_access"] is False
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


def test_guest_works_without_a_token_and_stays_in_its_sandbox(client, world, project, open_access):
    open_access()
    assert client.get("/api/v1/auth/config").json()["open_access"] is True
    who = me(client)
    assert who["guest"] is True and who["user"]["email"] == GUEST
    assert [(o["slug"], o["role"]) for o in who["organisations"]] == [("open-access", "member")]  # plain member
    # it can create and see its own project, as an editor of it
    org = sandbox_id(client)
    r = client.post("/api/v1/projects", json={"organisation_id": org, "name": "Guest project", "project_type": "general"})
    assert r.status_code == 201 and r.json()["my_role"] == "editor"
    listed = client.get("/api/v1/projects").json()["items"]
    assert [p["name"] for p in listed] == ["Guest project"]
    # nothing from another organisation is visible, by list or by address
    assert client.get(f"/api/v1/projects/{project}").status_code == 404
    assert client.get(f"/api/v1/projects/{project}/assets").status_code == 404
    # no administration: no new organisations (each would bring its own quotas and AI setting), no settings, no members
    assert client.post("/api/v1/organisations", json={"name": "Mine", "slug": "mine"}).status_code == 403
    assert client.patch(f"/api/v1/organisations/{org}/settings", json={"allow_external_ai": True}).status_code == 403
    assert client.post(f"/api/v1/organisations/{org}/members", json={"email": "x@example.test", "role": "admin",
                                                                      "password": "long-enough-password"}).status_code in (403, 404, 422)
    # no AI spend: the sandbox has external AI switched off
    st = client.get(f"/api/v1/projects/{listed[0]['id']}/assistant/status").json()
    assert st["available"] is False
    assert client.post(f"/api/v1/projects/{listed[0]['id']}/assistant/sessions").status_code == 503
    # the guest has no password, so it cannot be signed in as
    assert client.post("/api/v1/auth/login", json={"email": GUEST, "password": "anything-at-all"}).status_code == 401


def test_a_token_still_means_that_user_and_a_bad_token_is_refused(client, world, open_access):
    open_access()
    ok = client.get("/api/v1/auth/me", headers=world["editor"].h).json()
    assert ok["guest"] is False and ok["user"]["email"] == world["editor"].email
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_guest_uploads_and_storage_are_bounded(client, open_access):
    open_access(PARALLEL_OPEN_ACCESS_MAX_UPLOAD_BYTES=1000, PARALLEL_OPEN_ACCESS_STORAGE_CAP_BYTES=900)
    org = sandbox_id(client)
    pid = client.post("/api/v1/projects", json={"organisation_id": org, "name": "Limits", "project_type": "general"}).json()["id"]
    note = lambda n: {"file": (f"note{n}.md", b"x" * 500, "text/markdown")}  # noqa: E731
    big = client.post(f"/api/v1/projects/{pid}/assets", files={"file": ("big.md", b"x" * 1500, "text/markdown")})
    assert big.status_code == 413 and big.json()["error"]["code"] == "too_large"
    assert client.post(f"/api/v1/projects/{pid}/assets", files=note(1)).status_code == 201
    assert client.post(f"/api/v1/projects/{pid}/assets", files=note(2)).status_code == 201
    full = client.post(f"/api/v1/projects/{pid}/assets", files=note(3))
    assert full.status_code == 413 and full.json()["error"]["code"] == "open_access_storage_full"


def test_guest_reconstructions_are_capped(client, world, open_access):
    from parallel_core.db import session_scope
    from parallel_core.models import ReconstructionJob

    open_access(PARALLEL_OPEN_ACCESS_MAX_ACTIVE_RECONSTRUCTIONS=1)
    org = sandbox_id(client)
    pid = client.post("/api/v1/projects", json={"organisation_id": org, "name": "Busy", "project_type": "general"}).json()["id"]
    batch = client.post(f"/api/v1/projects/{pid}/capture-batches", json={"name": "Photos", "kind": "photos"}).json()["id"]
    with session_scope() as db:
        db.add(ReconstructionJob(project_id=pid, capture_batch_id=batch, status="running", stages=[], settings={}))
    r = client.post(f"/api/v1/projects/{pid}/reconstructions", json={"capture_batch_id": batch})
    assert r.status_code == 429 and r.json()["error"]["code"] == "open_access_busy"


def test_guest_sees_demonstrations_read_only_and_never_a_case(client, world, open_access):
    from parallel_api import demo

    open_access()
    try:
        res = demo.seed(only=["urban_drainage", "crashlens"])
        pid, crash = res["urban_drainage"]["project_id"], res["crashlens"]["project_id"]
        assert demo.grant_guest_access() >= 1  # idempotent
        names = {p["id"] for p in client.get("/api/v1/projects").json()["items"]}
        assert pid in names
        proj = client.get(f"/api/v1/projects/{pid}").json()
        assert proj["my_role"] == "viewer" and proj["demo_key"] == "urban_drainage"
        assert client.get(f"/api/v1/projects/{pid}/runs").status_code == 200
        # read only: it cannot edit, run, upload or compare in a demonstration
        assert client.post(f"/api/v1/projects/{pid}/scenarios", json={"name": "x"}).status_code in (403, 422)
        assert client.post(f"/api/v1/projects/{pid}/assets", files={"file": ("a.md", b"hi", "text/markdown")}).status_code == 403
        assert client.post(f"/api/v1/projects/{pid}/runs", json={}).status_code in (403, 422)
        # a case needs explicit assignment: the presenter sees the CrashLens case, the guest sees none of it
        from sqlalchemy import select

        from parallel_core.db import session_scope
        from parallel_core.models import User
        from parallel_core.security import issue_token

        with session_scope() as db:  # the presenter may already exist with another password, so mint its token
            token = issue_token(db.scalar(select(User.id).where(User.email == demo.PRESENTER_EMAIL)))
        cases = client.get(f"/api/v1/projects/{crash}/cases", headers={"Authorization": f"Bearer {token}"}).json()
        assert len(cases["items"] if isinstance(cases, dict) else cases) >= 1
        guest_cases = client.get(f"/api/v1/projects/{crash}/cases")
        assert guest_cases.status_code in (403, 404) or (
            (guest_cases.json()["items"] if isinstance(guest_cases.json(), dict) else guest_cases.json()) == [])
    finally:
        demo.reset()
