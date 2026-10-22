"""Optimisation studies over real SimPy runs: deterministic search, budgets, constraints, adoption."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def site(client, world):
    ed = world["editor"].h
    pid = client.post(
        "/api/v1/projects",
        headers=ed,
        json={
            "organisation_id": world["org_id"],
            "name": "Optimised site",
            "project_type": "construction",
            "template": "construction",
        },
    ).json()["id"]
    assert (
        client.post(
            f"/api/v1/projects/{pid}/members",
            headers=world["admin"].h,
            json={"user_id": world["viewer"].id, "role": "viewer"},
        ).status_code
        == 201
    )
    sc = client.get(f"/api/v1/projects/{pid}/scenarios", headers=ed).json()[0]
    return pid, sc


def study(client, h, pid, rev, **kw):
    body = {
        "name": "Bay capacity",
        "base_scenario_revision_id": rev,
        "objective_metric": "ops.mean_wait",
        "objective_entity": "bay",
        "direction": "minimise",
        "variables": [{"path": "controls.capacity_changes.bay", "values": [1, 2, 3]}],
        "budget": 3,
        "method": "grid",
        **kw,
    }
    return client.post(f"/api/v1/projects/{pid}/optimisations", headers=h, json=body)


def test_invalid_definitions_are_refused(client, world, site):
    pid, sc = site
    ed, rev = world["editor"].h, site[1]["current_revision_id"]
    r = study(client, ed, pid, rev, variables=[{"path": "settings.seed", "values": [1, 2]}])
    assert r.status_code == 422 and "seed" in str(r.json()["error"]["details"])
    r = study(client, ed, pid, rev, objective_metric="evac.t100", objective_entity=None)
    assert r.status_code == 422
    r = study(client, ed, pid, rev, objective_entity=None)
    assert r.status_code == 422 and "per entity" in str(r.json()["error"]["details"])
    r = study(client, ed, pid, rev, variables=[{"path": "controls.arrival_interval_scale", "values": [0, 1]}])
    assert r.status_code == 422  # 0 is outside the control's allowed range
    assert study(client, world["viewer"].h, pid, rev).status_code == 403


def test_grid_study_runs_real_candidates_and_reports_best_tested(client, world, site):
    pid, sc = site
    ed = world["editor"].h
    r = study(client, ed, pid, sc["current_revision_id"])
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["status"] == "completed", s
    cands = s["candidates"]
    assert len(cands) == 3 and all(c["run_state"] == "completed" for c in cands)
    assert [c["values"]["controls.capacity_changes.bay"] for c in cands] == [1, 2, 3]
    best = min((c for c in cands if c["feasible"]), key=lambda c: c["objective"])
    assert s["best_candidate_index"] == best["index"] and s["best_label"] == "Best tested candidate"
    assert s["stop_reason"] == "All 3 grid points evaluated."
    assert "no claim of a global optimum" in s["presentation_note"]
    # objective values are the runs' recorded metrics; all candidates share the base seed
    runs = [client.get(f"/api/v1/runs/{c['run_id']}", headers=ed).json() for c in cands]
    assert len({r["seed"] for r in runs}) == 1
    for c in cands:
        m = {
            (x["metric_id"], x["entity"]): x["value"]
            for x in client.get(f"/api/v1/runs/{c['run_id']}/metrics", headers=ed).json()
        }
        assert c["objective"] == m[("ops.mean_wait", "bay")]
        rev = client.get(f"/api/v1/scenario-revisions/{c['scenario_revision_id']}", headers=ed).json()
        assert (
            rev["origin"] == "optimisation"
            and rev["content"]["controls"]["capacity_changes"]["bay"]
            == c["values"]["controls.capacity_changes.bay"]
        )
    # the user's scenario was not modified
    assert (
        client.get(f"/api/v1/scenarios/{sc['id']}", headers=ed).json()["current_revision_id"]
        == sc["current_revision_id"]
    )
    # adopting a candidate creates a new, explicitly adopted scenario
    a = client.post(
        f"/api/v1/optimisations/{s['id']}/adopt",
        headers=ed,
        json={"candidate_index": best["index"], "scenario_name": "Adopted bay layout"},
    )
    assert a.status_code == 201, a.text
    assert a.json()["origin"] == "manual" and a.json()["adopted_by"] == world["editor"].id
    assert client.get(f"/api/v1/optimisations/{s['id']}", headers=world["viewer"].h).status_code == 200
    assert client.get(f"/api/v1/optimisations/{s['id']}", headers=world["outsider"].h).status_code == 404


def test_budget_and_constraints_are_honoured(client, world, site):
    pid, sc = site
    ed = world["editor"].h
    s = study(client, ed, pid, sc["current_revision_id"], budget=2, name="Small budget").json()
    assert len(s["candidates"]) == 2
    assert s["stop_reason"] == "Budget exhausted after 2 evaluations (3 grid points defined)."
    impossible = [{"metric_id": "ops.mean_wait", "entity": "gate", "op": "<=", "value": -1}]
    s = study(client, ed, pid, sc["current_revision_id"], constraints=impossible, name="Impossible").json()
    assert s["best_candidate_index"] is None and "No tested candidate satisfied" in s["stop_reason"]
    assert all(c["constraints"][0]["status"] == "violated" for c in s["candidates"])


def test_coordinate_search_moves_only_to_better_tested_points(client, world, site):
    pid, sc = site
    ed = world["editor"].h
    s = study(
        client,
        ed,
        pid,
        sc["current_revision_id"],
        method="coordinate",
        budget=8,
        name="Coordinate",
        variables=[
            {"path": "controls.capacity_changes.bay", "values": [1, 2]},
            {"path": "controls.capacity_changes.crane", "values": [1, 2]},
        ],
    ).json()
    assert s["status"] == "completed", s
    tested = [c for c in s["candidates"] if c["run_state"] == "completed"]
    assert 2 <= len(tested) <= 8
    best = s["candidates"][s["best_candidate_index"]]
    assert all(best["objective"] <= c["objective"] for c in tested if c["feasible"])
    assert s["stop_reason"]
