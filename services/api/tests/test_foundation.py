"""Foundation: authentication, organisations, projects, access control, assets, revisions, runs."""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np

from parallel_engines import reference_models as rm


def test_login_and_me(client, world):
    r = client.post("/api/v1/auth/login", json={"email": "editor@example.test", "password": "password123"})
    assert r.status_code == 200
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["user"]["email"] == "editor@example.test" and me["organisations"][0]["role"] == "member"
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "editor@example.test", "password": "nope"}
        ).status_code
        == 401
    )
    assert client.get("/api/v1/auth/me").status_code == 401
    body = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"}).json()
    assert body["error"]["code"] == "invalid_token"


def test_project_isolation_and_roles(client, world, project):
    out, viewer, reviewer, editor = (world[k] for k in ("outsider", "viewer", "reviewer", "editor"))
    assert client.get(f"/api/v1/projects/{project}", headers=out.h).status_code == 404
    assert client.get(f"/api/v1/projects/{project}", headers=viewer.h).json()["my_role"] == "viewer"
    # viewers and reviewers cannot edit
    r = client.patch(f"/api/v1/projects/{project}", headers=viewer.h, json={"name": "x"})
    assert r.status_code == 403
    assert (
        client.patch(
            f"/api/v1/projects/{project}", headers=editor.h, json={"description": "Level 3"}
        ).status_code
        == 200
    )
    listed = client.get("/api/v1/projects", headers=out.h).json()
    assert project not in [p["id"] for p in listed["items"]]
    # an investigator in the same org without project membership cannot see it
    assert client.get(f"/api/v1/projects/{project}", headers=world["investigator"].h).status_code == 404


def test_template_created_model_and_scenario(client, world, project):
    models = client.get(f"/api/v1/projects/{project}/models", headers=world["viewer"].h).json()
    assert len(models) == 1 and models[0]["discipline"] == "pedestrian"
    scenarios = client.get(f"/api/v1/projects/{project}/scenarios", headers=world["viewer"].h).json()
    assert scenarios[0]["is_baseline"] and scenarios[0]["engine_id"] == "jupedsim"


def test_geographic_crs_rejected_for_georeference(client, world, project):
    r = client.patch(
        f"/api/v1/projects/{project}",
        headers=world["editor"].h,
        json={"georeference": {"horizontal_crs": "EPSG:4326", "local_origin_projected": [0, 0]}},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_georeference"


def test_upload_verification_hash_and_signed_url(client, world, project):
    import tempfile
    from pathlib import Path

    from parallel_scene.ply import write_point_cloud

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cloud.ply"
        write_point_cloud(p, np.random.default_rng(1).normal(size=(50, 3)))
        data = p.read_bytes()
    r = client.post(
        f"/api/v1/projects/{project}/assets",
        headers=world["editor"].h,
        files={"file": ("cloud.ply", data, "application/octet-stream")},
    )
    assert r.status_code == 201, r.text
    a = r.json()
    import hashlib

    assert a["sha256"] == hashlib.sha256(data).hexdigest() and a["kind"] == "point_cloud"
    assert a["inspection"]["vertex_count"] == 50
    url = client.get(f"/api/v1/assets/{a['id']}/url", headers=world["viewer"].h).json()["url"]
    path = url.split("http://localhost:8000", 1)[1]
    got = client.get(path)
    assert got.status_code == 200 and got.content == data
    tampered = path.replace("sig=", "sig=x")
    assert client.get(tampered).status_code == 403
    # viewer cannot upload; disguised content rejected
    assert (
        client.post(
            f"/api/v1/projects/{project}/assets",
            headers=world["viewer"].h,
            files={"file": ("x.ply", data, "application/octet-stream")},
        ).status_code
        == 403
    )
    bad = client.post(
        f"/api/v1/projects/{project}/assets",
        headers=world["editor"].h,
        files={"file": ("fake.png", b"not an image at all", "image/png")},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "upload_rejected"
    exe = client.post(
        f"/api/v1/projects/{project}/assets",
        headers=world["editor"].h,
        files={"file": ("run.sh", b"#!/bin/sh\nrm -rf /", "text/plain")},
    )
    assert exe.status_code == 422


def test_published_revisions_are_immutable_and_edits_create_revisions(client, world, project):
    ed = world["editor"].h
    dm = client.get(f"/api/v1/projects/{project}/models", headers=ed).json()[0]
    rev = client.get(f"/api/v1/model-revisions/{dm['current_revision_id']}", headers=ed).json()
    assert rev["status"] == "draft"
    pub = client.post(f"/api/v1/model-revisions/{rev['id']}/publish", headers=ed).json()
    assert pub["status"] == "published"
    assert client.post(f"/api/v1/model-revisions/{rev['id']}/publish", headers=ed).status_code == 409
    content = rev["content"]
    content["content"]["movement"]["time_gap_s"] = 0.9
    r = client.post(
        f"/api/v1/models/{dm['id']}/revisions", headers=ed, json={"content": content, "message": "faster"}
    )
    assert r.status_code == 201
    new = r.json()
    assert new["number"] == rev["number"] + 1 and new["status"] == "draft"
    old = client.get(f"/api/v1/model-revisions/{rev['id']}", headers=ed).json()
    assert old["content"]["content"]["movement"]["time_gap_s"] == 1.0  # history untouched


def test_run_lifecycle_real_execution_and_results(client, world, project):
    ed = world["editor"].h
    sc = client.get(f"/api/v1/projects/{project}/scenarios", headers=ed).json()[0]
    rev_id = sc["current_revision_id"]
    r = client.post(
        f"/api/v1/projects/{project}/runs",
        headers={**ed, "Idempotency-Key": "baseline-1"},
        json={"scenario_revision_id": rev_id},
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["state"] == "completed", run
    assert [h["state"] for h in run["state_history"]] == [
        "draft",
        "validating",
        "queued",
        "preparing",
        "running",
        "collecting",
        "postprocessing",
        "completed",
    ]
    assert run["engine_version"] == "1.4.2" and run["input_hashes"]
    # idempotent resubmission returns the same run
    again = client.post(
        f"/api/v1/projects/{project}/runs",
        headers={**ed, "Idempotency-Key": "baseline-1"},
        json={"scenario_revision_id": rev_id},
    ).json()
    assert again["id"] == run["id"]
    metrics = {
        (m["metric_id"], m["entity"]): m
        for m in client.get(f"/api/v1/runs/{run['id']}/metrics", headers=ed).json()
    }
    n = metrics[("evac.initial_population", None)]["value"]
    assert (
        n
        == metrics[("evac.evacuated", None)]["value"]
        + metrics[("evac.unreachable", None)]["value"]
        + metrics[("evac.remaining", None)]["value"]
    )
    envelope = client.get(f"/api/v1/runs/{run['id']}/data/result.json", headers=world["viewer"].h).json()
    assert envelope["revisions"]["scenario_revision_id"] == rev_id
    assert envelope["engine"]["engine_version"] == "1.4.2" and envelope["input_hashes"]
    assert all(c["passed"] for c in envelope["native_crosschecks"])
    arts = client.get(f"/api/v1/runs/{run['id']}/artifacts", headers=ed).json()
    kinds = {a["kind"] for a in arts}
    assert {"input", "raw_output", "trajectory", "log", "normalised"} <= kinds
    # the scenario revision used was frozen
    used = client.get(f"/api/v1/scenario-revisions/{rev_id}", headers=ed).json()
    assert used["status"] == "published"
    # outsider cannot read results
    assert client.get(f"/api/v1/runs/{run['id']}", headers=world["outsider"].h).status_code == 404


def test_invalid_scenario_fails_validation_without_execution(client, world, project):
    ed = world["editor"].h
    sc = client.get(f"/api/v1/projects/{project}/scenarios", headers=ed).json()[0]
    rev = client.get(f"/api/v1/scenario-revisions/{sc['current_revision_id']}", headers=ed).json()
    content = rev["content"]
    content["controls"]["closed_exits"] = ["does_not_exist"]
    new = client.post(f"/api/v1/scenarios/{sc['id']}/revisions", headers=ed, json={"content": content}).json()
    run = client.post(
        f"/api/v1/projects/{project}/runs", headers=ed, json={"scenario_revision_id": new["id"]}
    ).json()
    assert run["state"] == "failed" and run["error_code"] == "validation_failed"
    assert [h["state"] for h in run["state_history"]] == ["draft", "validating", "failed"]


def test_unavailable_engine_submission_is_refused(client, world, project):
    ed = world["editor"].h
    env = rm.mine_ventilation_model()
    r = client.post(
        f"/api/v1/projects/{project}/models",
        headers=ed,
        json={"name": "Ventilation", "content": env.model_dump(mode="json")},
    )
    assert r.status_code == 201
    dm_id = r.json()["domain_model_id"]
    sc = client.post(
        f"/api/v1/projects/{project}/scenarios",
        headers=ed,
        json={
            "domain_model_id": dm_id,
            "name": "Vent baseline",
            "content": {
                "discipline": "ventilation",
                "engine_id": "ventsim",
                "controls": {"kind": "ventilation"},
            },
        },
    ).json()
    r = client.post(f"/api/v1/projects/{project}/runs", headers=ed, json={"scenario_revision_id": sc["id"]})
    assert r.status_code == 409
    assert r.json()["error"]["details"]["availability"] == "licence_required"


def test_file_exchange_run_awaits_operator_and_imports_with_provenance(client, world, project):
    admin, ed = world["admin"].h, world["editor"].h
    # register a licensed installation so the exchange becomes available
    r = client.post(
        f"/api/v1/organisations/{world['org_id']}/engine-installations",
        headers=admin,
        json={
            "engine_id": "ventsim",
            "label": "Mine office workstation",
            "edition": "Ventsim Design (test record)",
            "licence_confirmed": True,
            "operator_contact": "vent engineer",
        },
    )
    assert r.status_code == 201 and r.json()["last_check"]["availability"] == "ready"
    scs = [
        s
        for s in client.get(f"/api/v1/projects/{project}/scenarios", headers=ed).json()
        if s["engine_id"] == "ventsim"
    ]
    run = client.post(
        f"/api/v1/projects/{project}/runs",
        headers=ed,
        json={"scenario_revision_id": scs[0]["current_revision_id"]},
    ).json()
    assert run["state"] == "awaiting_external_result" and run["execution_mode"] == "file_exchange"
    url = client.get(f"/api/v1/runs/{run['id']}/exchange-package", headers=ed).json()["url"]
    pkg = client.get(url.split("http://localhost:8000", 1)[1]).content
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(pkg)).read("manifest.json"))
    assert "not automated execution" in manifest["label"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "airway_results.csv",
            "airway_id,airflow_m3s,pressure_drop_pa\nA1,48.5,300\nA2,30,90\nA3,18.5,60\n",
        )
        z.writestr("metadata.json", json.dumps({"software_version": "test", "operator": "vent engineer"}))
    r = client.post(
        f"/api/v1/runs/{run['id']}/external-result",
        headers=ed,
        files={"file": ("result.zip", buf.getvalue(), "application/zip")},
        data={
            "external_installation": "Mine office workstation (Ventsim Design)",
            "supplied_provenance": "Operator ran the network on 2026-10-22",
        },
    )
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["state"] == "completed" and done["external_installation"].startswith("Mine office")
    env = client.get(f"/api/v1/runs/{run['id']}/data/result.json", headers=ed).json()
    assert env["imported"]["imported_by"] == world["editor"].id and env["imported"]["package_sha256"]
    assert env["execution_mode"] == "file_exchange"
    m = client.get(f"/api/v1/runs/{run['id']}/metrics", headers=ed).json()
    assert m[0]["metric_id"] == "vent.total_airflow" and m[0]["value"] == 48.5


def test_engines_listing_reports_availability(client, world):
    engines = client.get(
        f"/api/v1/engines?organisation_id={world['org_id']}", headers=world["viewer"].h
    ).json()
    by = {e["capability"]["engine_id"]: e for e in engines}
    assert len(by) == 15
    assert by["jupedsim"]["availability"][0]["availability"] == "ready"
    assert by["pccrash"]["availability"][0]["availability"] == "integration_incomplete"
    assert by["isaacsim"]["availability"][0]["availability"] in (
        "worker_unavailable",
        "configuration_required",
    )


def test_audit_events_recorded(client, world, project):
    acts = client.get(f"/api/v1/projects/{project}/activity", headers=world["viewer"].h).json()["items"]
    actions = {a["action"] for a in acts}
    assert {"project.created", "asset.uploaded", "run.submitted"} <= actions


def test_openapi_document(client):
    doc = client.get("/api/v1/openapi.json").json()
    assert doc["info"]["title"] == "Parallel API" and "/api/v1/projects/{project_id}/runs" in doc["paths"]


def test_admin_command_bootstraps_first_administrator(client, monkeypatch):
    from parallel_api import admin

    monkeypatch.setenv("PARALLEL_BOOTSTRAP_PASSWORD", "bootstrap-pass-0123")
    out = admin.create_admin("Ops@Example.org", "Operations", "Example Engineering", None)
    assert out["user_created"] and out["organisation_created"] and out["slug"] == "example-engineering"
    again = admin.create_admin("ops@example.org", "Operations", "Example Engineering", None)
    assert not again["user_created"] and not again["organisation_created"]  # idempotent
    tok = client.post("/api/v1/auth/login", json={"email": "ops@example.org", "password": "bootstrap-pass-0123"})
    assert tok.status_code == 200
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok.json()['access_token']}"}).json()
    assert any(m["role"] == "admin" for m in me["organisations"])
    monkeypatch.setenv("PARALLEL_BOOTSTRAP_PASSWORD", "short")
    import pytest

    with pytest.raises(SystemExit):
        admin.set_password("ops@example.org")
