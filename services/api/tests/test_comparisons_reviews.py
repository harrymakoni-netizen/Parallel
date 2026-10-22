"""Controlled comparisons over real JuPedSim runs, and scoped review records."""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path

import pytest

from parallel_engines import reference_models as rm


@pytest.fixture(scope="module")
def mine(client, world):
    ed, admin = world["editor"].h, world["admin"].h
    r = client.post(
        "/api/v1/projects",
        headers=ed,
        json={
            "organisation_id": world["org_id"],
            "name": "Comparison mine",
            "project_type": "mine_evacuation",
            "template": "mine_evacuation",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    for who, role in ((world["reviewer"], "reviewer"), (world["viewer"], "viewer")):
        assert (
            client.post(
                f"/api/v1/projects/{pid}/members", headers=admin, json={"user_id": who.id, "role": role}
            ).status_code
            == 201
        )
    base_sc = client.get(f"/api/v1/projects/{pid}/scenarios", headers=ed).json()[0]
    dm_id = base_sc["domain_model_id"]

    def scenario(name: str, content) -> str:
        r = client.post(
            f"/api/v1/projects/{pid}/scenarios",
            headers=ed,
            json={"domain_model_id": dm_id, "name": name, "content": content.model_dump(mode="json")},
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def run(rev_id: str, key: str) -> dict:
        r = client.post(
            f"/api/v1/projects/{pid}/runs",
            headers={**ed, "Idempotency-Key": key},
            json={"scenario_revision_id": rev_id},
        )
        assert r.status_code == 201, r.text
        return r.json()

    runs = {
        "baseline": run(base_sc["current_revision_id"], "cmp-base"),
        "west_closed": run(
            scenario("West portal closed", rm.pedestrian_scenario(closed_exits=["west_portal"])), "cmp-west"
        ),
        "seed7": run(scenario("Baseline, other seed", rm.pedestrian_scenario(seed=7)), "cmp-seed7"),
        "invalid": run(scenario("Invalid", rm.pedestrian_scenario(closed_exits=["nope"])), "cmp-invalid"),
    }
    assert all(runs[k]["state"] == "completed" for k in ("baseline", "west_closed", "seed7"))
    assert runs["invalid"]["state"] == "failed"
    return pid, runs


def test_controlled_comparison_of_real_runs(client, world, mine):
    pid, runs = mine
    body = {
        "name": "West portal closure",
        "baseline_run_id": runs["baseline"]["id"],
        "run_ids": [runs["west_closed"]["id"]],
    }
    assert (
        client.post(f"/api/v1/projects/{pid}/comparisons", headers=world["viewer"].h, json=body).status_code
        == 403
    )
    r = client.post(f"/api/v1/projects/{pid}/comparisons", headers=world["reviewer"].h, json=body)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["compatibility"]["controlled"], c["compatibility"]["checks"]
    pop = next(x for x in c["compatibility"]["checks"] if x["check"] == "Population / demand / rainfall")
    assert pop["status"] == "ok"  # same seed and start zones -> identical materialised population
    rows = {(x["metric_id"], x["entity"]): x for x in c["table"]}
    base_n = rows[("evac.initial_population", None)]["baseline"]["value"]
    cell = next(iter(rows[("evac.initial_population", None)]["candidates"].values()))
    assert cell["value"] == base_n and cell["abs_diff"] == 0
    # metric values in the table are the stored run metrics, not recomputed ones
    stored = {
        (m["metric_id"], m["entity"]): m["value"]
        for m in client.get(
            f"/api/v1/runs/{runs['west_closed']['id']}/metrics", headers=world["viewer"].h
        ).json()
    }
    t100 = next(iter(rows[("evac.t100", None)]["candidates"].values()))
    assert t100["value"] == stored[("evac.t100", None)]
    if t100["abs_diff"] is not None:
        assert t100["abs_diff"] == pytest.approx(
            stored[("evac.t100", None)] - rows[("evac.t100", None)]["baseline"]["value"]
        )
    # CSV export carries the same table
    out = client.get(f"/api/v1/comparisons/{c['id']}/csv", headers=world["viewer"].h)
    assert out.status_code == 200 and out.headers["content-type"].startswith("text/csv")
    table = list(csv.reader(io.StringIO(out.text)))
    assert table[0][:3] == ["metric_id", "entity", "unit"] and len(table) == len(c["table"]) + 1
    listed = client.get(f"/api/v1/projects/{pid}/comparisons", headers=world["viewer"].h).json()
    assert c["id"] in [x["id"] for x in listed]
    assert client.get(f"/api/v1/comparisons/{c['id']}", headers=world["outsider"].h).status_code == 404


def test_uncontrolled_comparison_is_flagged(client, world, mine):
    pid, runs = mine
    c = client.post(
        f"/api/v1/projects/{pid}/comparisons",
        headers=world["editor"].h,
        json={
            "name": "Seed sensitivity",
            "baseline_run_id": runs["baseline"]["id"],
            "run_ids": [runs["seed7"]["id"]],
        },
    ).json()
    comp = c["compatibility"]
    assert not comp["controlled"]
    flagged = {x["check"] for x in comp["checks"] if x["status"] == "warning"}
    assert {"Seed strategy", "Population / demand / rainfall"} <= flagged
    assert "Not fully controlled" in comp["summary"]


def test_comparison_requires_completed_runs_in_project(client, world, mine):
    pid, runs = mine
    r = client.post(
        f"/api/v1/projects/{pid}/comparisons",
        headers=world["editor"].h,
        json={"name": "x", "baseline_run_id": runs["baseline"]["id"], "run_ids": [runs["invalid"]["id"]]},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "run_not_completed"
    r = client.post(
        f"/api/v1/projects/{pid}/comparisons",
        headers=world["editor"].h,
        json={"name": "x", "baseline_run_id": runs["baseline"]["id"], "run_ids": [runs["baseline"]["id"]]},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "nothing_to_compare"
    # a run from another project is not found here (own project: the shared fixture must stay unfrozen)
    project = client.post(
        "/api/v1/projects",
        headers=world["editor"].h,
        json={
            "organisation_id": world["org_id"],
            "name": "Other mine",
            "project_type": "mine_evacuation",
            "template": "mine_evacuation",
        },
    ).json()["id"]
    other = client.post(
        f"/api/v1/projects/{project}/runs",
        headers={**world["editor"].h, "Idempotency-Key": "cmp-other"},
        json={
            "scenario_revision_id": client.get(
                f"/api/v1/projects/{project}/scenarios", headers=world["editor"].h
            ).json()[0]["current_revision_id"]
        },
    ).json()
    r = client.post(
        f"/api/v1/projects/{pid}/comparisons",
        headers=world["editor"].h,
        json={"name": "x", "baseline_run_id": runs["baseline"]["id"], "run_ids": [other["id"]]},
    )
    assert r.status_code == 404


def test_scoped_reviews_and_permissions(client, world, mine):
    pid, runs = mine
    body = {
        "object_type": "simulation_run",
        "object_id": runs["west_closed"]["id"],
        "scope": "model_assumptions",
        "status": "approved",
        "comment": "Exit closure applied as intended.",
    }
    assert client.post("/api/v1/reviews", headers=world["viewer"].h, json=body).status_code == 403
    assert client.post("/api/v1/reviews", headers=world["editor"].h, json=body).status_code == 403
    assert client.post("/api/v1/reviews", headers=world["outsider"].h, json=body).status_code == 404
    r = client.post("/api/v1/reviews", headers=world["reviewer"].h, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["reviewer_id"] == world["reviewer"].id
    listed = client.get(
        f"/api/v1/projects/{pid}/reviews?object_id={runs['west_closed']['id']}", headers=world["viewer"].h
    ).json()
    assert len(listed) == 1 and listed[0]["scope_note"].startswith("Model assumptions only")
    bad = client.post("/api/v1/reviews", headers=world["reviewer"].h, json={**body, "scope": "everything"})
    assert bad.status_code == 422
    acts = client.get(f"/api/v1/projects/{pid}/activity", headers=world["viewer"].h).json()["items"]
    assert "review.recorded" in {a["action"] for a in acts}


def test_comparison_report_pdf_csv_manifest(client, world, mine):
    import hashlib

    from pypdf import PdfReader

    pid, runs = mine
    ed = world["editor"].h
    comp = client.post(
        f"/api/v1/projects/{pid}/comparisons",
        headers=ed,
        json={
            "name": "Exit closure report basis",
            "baseline_run_id": runs["baseline"]["id"],
            "run_ids": [runs["west_closed"]["id"]],
        },
    ).json()
    body = {
        "kind": "comparison",
        "title": "West portal closure study",
        "comparison_id": comp["id"],
        "narrative": "Closing the west portal moves the face crew to the east portal.",
        "narrative_origin": "manual",
    }
    assert (
        client.post(f"/api/v1/projects/{pid}/reports", headers=world["viewer"].h, json=body).status_code
        == 403
    )
    r = client.post(f"/api/v1/projects/{pid}/reports", headers=ed, json=body)
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["status"] == "completed", rep["error"]
    assert rep["run_ids"] == [runs["baseline"]["id"], runs["west_closed"]["id"]]
    man = rep["manifest"]
    # spec 27 fields
    assert man["project"]["name"] == "Comparison mine"
    assert [s["ref"] for s in man["runs"]] == ["R1", "R2"]
    s2 = man["runs"][1]
    assert s2["scenario"]["name"] == "West portal closed" and s2["scenario"]["revision_id"]
    assert s2["engine"]["engine_version"] == "1.4.2" and s2["engine"]["adapter_version"]
    assert s2["execution_mode"] == "managed" and s2["input_hashes"]
    assert s2["sources"]["counts"].get("synthetic", 0) > 0  # the reference mine is synthetic
    assert man["comparison"]["controlled"] is True and man["comparison"]["baseline_ref"] == "R1"
    assert man["report"]["review_status"] == "not reviewed" and man["report"]["author"]["name"] == "Editor"
    assert any("synthetic" in w for w in man["warnings"])

    def fetch(name: str) -> bytes:
        url = client.get(f"/api/v1/reports/{rep['id']}/files/{name}", headers=world["viewer"].h).json()["url"]
        got = client.get(url.split("http://localhost:8000", 1)[1])
        assert got.status_code == 200
        assert hashlib.sha256(got.content).hexdigest() == man["files"][name]["sha256"]
        return got.content

    pdf = fetch("report.pdf")
    if os.environ.get("PARALLEL_REPORT_DUMP"):  # visual inspection aid
        Path(os.environ["PARALLEL_REPORT_DUMP"]).write_bytes(pdf)
    text = "\n".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf)).pages)
    for needle in (
        "West portal closure study",
        "Comparison basis",
        "People evacuated over time",
        runs["west_closed"]["id"],
        "Synthetic demonstration material",
        "Traceability",
        "Reviews",
    ):
        assert needle in text, needle
    rows = list(csv.DictReader(io.StringIO(fetch("metrics.csv").decode())))
    stored = client.get(f"/api/v1/runs/{runs['west_closed']['id']}/metrics", headers=ed).json()
    t100 = next(m["value"] for m in stored if m["metric_id"] == "evac.t100")
    row = next(x for x in rows if x["run_ref"] == "R2" and x["metric_id"] == "evac.t100")
    assert float(row["value"]) == t100
    import json

    assert json.loads(fetch("manifest.json"))["report"]["id"] == rep["id"]
    # a scoped review of the report updates its review status
    rv = client.post(
        "/api/v1/reviews",
        headers=world["reviewer"].h,
        json={"object_type": "report", "object_id": rep["id"], "scope": "report", "status": "approved"},
    )
    assert rv.status_code == 201
    assert (
        client.get(f"/api/v1/reports/{rep['id']}", headers=ed).json()["review_status"] == "approved (report)"
    )
    assert client.get(f"/api/v1/reports/{rep['id']}", headers=world["outsider"].h).status_code == 404


def test_report_rejects_incomplete_runs_and_requires_narrative_origin(client, world, mine):
    pid, runs = mine
    ed = world["editor"].h
    r = client.post(
        f"/api/v1/projects/{pid}/reports",
        headers=ed,
        json={"kind": "project", "title": "x", "run_ids": [runs["invalid"]["id"]]},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "run_not_completed"
    r = client.post(
        f"/api/v1/projects/{pid}/reports",
        headers=ed,
        json={"kind": "project", "title": "x", "run_ids": [runs["baseline"]["id"]], "narrative": "text"},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "narrative_origin_required"
