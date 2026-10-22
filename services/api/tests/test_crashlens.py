"""CrashLens: explicit case access, preserved originals, observations vs interpretations,
hypotheses tested against evidence through a real Virtual CRASH file exchange, and the case report."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import zipfile
from pathlib import Path

import pytest
from PIL import Image


def png(seed: int) -> bytes:
    im = Image.new("RGB", (64, 48), (seed * 40 % 255, 90, 160))
    for x in range(0, 64, 8):
        im.putpixel((x, seed % 48), (255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def crash(client, world):
    ed, admin = world["editor"].h, world["admin"].h
    pid = client.post(
        "/api/v1/projects",
        headers=ed,
        json={
            "organisation_id": world["org_id"],
            "name": "Training junction",
            "project_type": "crashlens",
            "template": "crashlens",
        },
    ).json()["id"]
    for who, role in (("investigator", "viewer"), ("reviewer", "reviewer"), ("viewer", "viewer")):
        r = client.post(
            f"/api/v1/projects/{pid}/members", headers=admin, json={"user_id": world[who].id, "role": role}
        )
        assert r.status_code == 201, r.text
    r = client.post(
        f"/api/v1/projects/{pid}/cases",
        headers=ed,
        json={"reference": "TC-001", "title": "Controlled training scene", "training": True},
    )
    assert r.status_code == 201, r.text
    case = r.json()
    assert case["my_role"] == "investigator"
    for who, role in (("investigator", "investigator"), ("reviewer", "reviewer")):
        r = client.post(
            f"/api/v1/cases/{case['id']}/assignments",
            headers=ed,
            json={"user_id": world[who].id, "role": role},
        )
        assert r.status_code == 201, r.text
    return {"project": pid, "case": case["id"], "state": {}}


def test_case_access_requires_explicit_assignment(client, world, crash):
    cid, pid = crash["case"], crash["project"]
    for who in ("viewer", "admin", "outsider"):
        assert client.get(f"/api/v1/cases/{cid}", headers=world[who].h).status_code == 404, who
    assert client.get(f"/api/v1/projects/{pid}/cases", headers=world["viewer"].h).json() == []
    assert [
        c["id"] for c in client.get(f"/api/v1/projects/{pid}/cases", headers=world["investigator"].h).json()
    ] == [cid]
    # only project members can be assigned; unassigned members cannot assign
    r = client.post(
        f"/api/v1/cases/{cid}/assignments",
        headers=world["editor"].h,
        json={"user_id": world["outsider"].id, "role": "reviewer"},
    )
    assert r.status_code == 422
    r = client.post(
        f"/api/v1/cases/{cid}/assignments",
        headers=world["viewer"].h,
        json={"user_id": world["viewer"].id, "role": "investigator"},
    )
    assert r.status_code == 404
    # a case reviewer cannot edit case content
    r = client.post(
        f"/api/v1/cases/{cid}/timeline",
        headers=world["reviewer"].h,
        json={"t_offset_s": 0, "description": "x happened", "source_type": "record"},
    )
    assert r.status_code == 403
    # case activity never leaks into the project feed of unassigned members
    feed = client.get(f"/api/v1/projects/{pid}/activity", headers=world["viewer"].h).json()["items"]
    assert not any(e["details"].get("case_id") for e in feed)
    assert any(
        e["action"] == "case.created"
        for e in client.get(f"/api/v1/cases/{cid}/activity", headers=world["investigator"].h).json()
    )


def test_evidence_originals_are_preserved(client, world, crash):
    from sqlalchemy import text

    from parallel_core.db import session_scope

    cid, inv = crash["case"], world["investigator"].h
    photo = png(1)
    r = client.post(
        f"/api/v1/cases/{cid}/evidence",
        headers=inv,
        data={
            "kind": "image",
            "title": "Scene photo 1",
            "captured_by": "Officer A",
            "source_metadata": json.dumps({"device": "training camera"}),
        },
        files={"file": ("scene1.png", photo, "image/png")},
    )
    assert r.status_code == 201, r.text
    ev = r.json()
    assert ev["original_sha256"] == hashlib.sha256(photo).hexdigest()
    assert "does not establish" in ev["integrity_note"]
    crash["state"]["evidence"] = ev["id"]
    bad = client.post(
        f"/api/v1/cases/{cid}/evidence",
        headers=inv,
        data={"kind": "video", "title": "Not a video"},
        files={"file": ("x.png", png(2), "image/png")},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "kind_mismatch"
    assert (
        client.post(
            f"/api/v1/cases/{cid}/evidence", headers=inv, data={"kind": "image", "title": "No file"}
        ).status_code
        == 422
    )
    # evidence media is not reachable through ordinary project asset routes
    asset_id = ev["source_asset_id"]
    assert client.get(f"/api/v1/assets/{asset_id}", headers=world["editor"].h).status_code == 404
    listed = client.get(f"/api/v1/projects/{crash['project']}/assets", headers=world["editor"].h).json()[
        "items"
    ]
    assert asset_id not in [a["id"] for a in listed]
    v = client.post(f"/api/v1/evidence/{ev['id']}/verify", headers=inv).json()
    assert v["matches"] and v["current_sha256"] == ev["original_sha256"]
    # derivative: a new file linked to the original's hash; the original is untouched
    redacted = png(3)
    d = client.post(
        f"/api/v1/evidence/{ev['id']}/derivatives",
        headers=inv,
        data={"kind": "redaction", "method": "Number plate blurred (training tool, 12 px Gaussian)"},
        files={"file": ("scene1-redacted.png", redacted, "image/png")},
    )
    assert d.status_code == 201, d.text
    assert d.json()["source_sha256"] == ev["original_sha256"]
    same = client.post(
        f"/api/v1/evidence/{ev['id']}/derivatives",
        headers=inv,
        data={"kind": "crop", "method": "no change at all"},
        files={"file": ("copy.png", photo, "image/png")},
    )
    assert same.status_code == 422
    assert (
        client.get(f"/api/v1/evidence/{ev['id']}", headers=inv).json()["original_sha256"]
        == ev["original_sha256"]
    )
    url = client.get(f"/api/v1/evidence/{ev['id']}/original-url", headers=inv).json()["url"]
    assert client.get(url.split("http://localhost:8000", 1)[1]).content == photo
    actions = [h["action"] for h in client.get(f"/api/v1/evidence/{ev['id']}/handling", headers=inv).json()]
    assert actions == ["received", "integrity_verified", "derivative_created", "accessed"]
    # defence in depth: the database refuses to rewrite an original's hash
    with pytest.raises(Exception, match="immutable"), session_scope() as db:
        db.execute(text("UPDATE evidence_item SET original_sha256 = 'x' WHERE id = :i"), {"i": ev["id"]})
    with pytest.raises(Exception, match="immutable"), session_scope() as db:
        db.execute(text("UPDATE source_asset SET sha256 = 'x' WHERE id = :i"), {"i": asset_id})
    assert client.get(f"/api/v1/evidence/{ev['id']}", headers=world["viewer"].h).status_code == 404


def test_observations_interpretations_and_skid_speed(client, world, crash):
    cid, inv, ev = crash["case"], world["investigator"].h, crash["state"]["evidence"]
    base = {
        "statement_type": "observation",
        "kind": "skid_mark",
        "text": "Dark tyre mark on the eastbound lane",
    }
    assert client.post(f"/api/v1/cases/{cid}/observations", headers=inv, json=base).status_code == 422
    r = client.post(f"/api/v1/cases/{cid}/observations", headers=inv, json={**base, "evidence_item_id": ev})
    assert r.json()["error"]["code"] == "spatial_reference_required"
    skid = client.post(
        f"/api/v1/cases/{cid}/observations",
        headers=inv,
        json={
            **base,
            "evidence_item_id": ev,
            "geometry": {"type": "LineString", "coordinates": [[-14.0, -1.6], [10.5, -1.4]]},
            "measurement_value": 24.5,
            "measurement_unit": "m",
            "uncertainty": {"kind": "absolute", "value": 0.3, "unit": "m", "note": "tape, two readings"},
            "limitation": "Start of the mark is faint in photo 1",
        },
    )
    assert skid.status_code == 201, skid.text
    skid = skid.json()
    r = client.post(
        f"/api/v1/cases/{cid}/observations",
        headers=inv,
        json={"statement_type": "interpretation", "kind": "skid_mark", "text": "Braking began early"},
    )
    assert r.json()["error"]["code"] == "basis_required"
    calc = client.post(
        f"/api/v1/cases/{cid}/calculations/skid-speed",
        headers=inv,
        json={
            "observation_id": skid["id"],
            "friction_low": 0.6,
            "friction_high": 0.8,
            "friction_basis": "Dry asphalt range, training assumption",
        },
    )
    assert calc.status_code == 201, calc.text
    c = calc.json()
    assert c["statement_type"] == "interpretation" and c["based_on"] == [skid["id"]]

    def v_kmh(mu: float) -> float:  # independent check of v = sqrt(2 mu g d)
        return math.sqrt(2 * mu * 9.80665 * 24.5) * 3.6

    assert c["uncertainty"]["lower"] == pytest.approx(v_kmh(0.6)) == pytest.approx(61.13, abs=0.01)
    assert c["uncertainty"]["upper"] == pytest.approx(v_kmh(0.8))
    assert "Not a determination of fault" in c["limitation"]
    # reviewer records a scoped review of the observation; cannot author observations
    assert (
        client.post(
            f"/api/v1/cases/{cid}/observations",
            headers=world["reviewer"].h,
            json={**base, "evidence_item_id": ev, "position": [0, 0]},
        ).status_code
        == 403
    )
    rv = client.post(
        "/api/v1/reviews",
        headers=world["reviewer"].h,
        json={
            "object_type": "evidence_observation",
            "object_id": skid["id"],
            "scope": "evidence_annotations",
            "status": "approved",
        },
    )
    assert rv.status_code == 201, rv.text
    obs = {o["id"]: o for o in client.get(f"/api/v1/cases/{cid}/observations", headers=inv).json()}
    assert obs[skid["id"]]["review_status"] == "reviewed"
    assert [x["object_id"] for x in client.get(f"/api/v1/cases/{cid}/reviews", headers=inv).json()] == [
        skid["id"]
    ]
    # the project review list (visible to all project members) never shows case reviews
    assert client.get(f"/api/v1/projects/{crash['project']}/reviews", headers=world["viewer"].h).json() == []


def test_hypotheses_tested_against_evidence_and_case_report(client, world, crash):
    from pypdf import PdfReader

    cid, pid, inv, ev = crash["case"], crash["project"], world["investigator"].h, crash["state"]["evidence"]
    prov = {"kind": "imported", "source": "Training vehicle data sheet"}
    vehicles = {}
    for label, mass, length in (("Vehicle 1", 1350, 4.3), ("Vehicle 2", 1600, 4.6)):
        r = client.post(
            f"/api/v1/cases/{cid}/vehicles",
            headers=inv,
            json={
                "label": label,
                "mass_kg": mass,
                "length_m": length,
                "width_m": 1.8,
                "data_source": "Training data sheet",
                "provenance": prov,
            },
        )
        assert r.status_code == 201, r.text
        vehicles[label] = r.json()["id"]
    rest = {}
    for label, pos in (("Vehicle 1", [12.0, 1.0]), ("Vehicle 2", [6.0, -2.0])):
        r = client.post(
            f"/api/v1/cases/{cid}/observations",
            headers=inv,
            json={
                "statement_type": "observation",
                "kind": "final_position",
                "text": f"{label} at rest",
                "evidence_item_id": ev,
                "position": pos,
                "uncertainty": {"kind": "absolute", "value": 0.5, "unit": "m"},
            },
        )
        assert r.status_code == 201, r.text
        rest[label] = r.json()["id"]
    hyps = {}
    for label, desc in (
        ("H1 eastbound braking", "Vehicle 1 braked before impact"),
        ("H2 no braking", "Vehicle 1 did not brake before impact"),
    ):
        r = client.post(
            f"/api/v1/cases/{cid}/hypotheses",
            headers=inv,
            json={
                "label": label,
                "content": {
                    "description": desc,
                    "participant_ids": list(vehicles.values()),
                    "unresolved_questions": ["Signal phase at impact"],
                },
            },
        )
        assert r.status_code == 201, r.text
        hyps[label[:2]] = r.json()["id"]
    r = client.post(
        f"/api/v1/hypotheses/{hyps['H1']}/links",
        headers=inv,
        json={"observation_id": rest["Vehicle 1"], "relation": "supports", "rationale": "Rest position fits"},
    )
    assert r.status_code == 201
    assert (
        client.post(
            f"/api/v1/hypotheses/{hyps['H1']}/links",
            headers=inv,
            json={"observation_id": rest["Vehicle 1"], "relation": "supports", "rationale": "again"},
        ).status_code
        == 409
    )
    # collision model built from the case's own observations
    r = client.post(
        f"/api/v1/cases/{cid}/collision-model",
        headers=inv,
        json={
            "name": "TC-001 collision model",
            "friction_low": 0.65,
            "friction_high": 0.8,
            "friction_basis": "Dry asphalt, training assumption",
            "vehicle_record_ids": list(vehicles.values()),
            "targets": [
                {
                    "observation_id": rest["Vehicle 1"],
                    "kind": "rest_position",
                    "vehicle_record_id": vehicles["Vehicle 1"],
                },
                {
                    "observation_id": rest["Vehicle 2"],
                    "kind": "rest_position",
                    "vehicle_record_id": vehicles["Vehicle 2"],
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    dm_id = r.json()["domain_model_id"]
    # case-scoped models are invisible to unassigned project members
    assert dm_id not in [
        m["id"] for m in client.get(f"/api/v1/projects/{pid}/models", headers=world["viewer"].h).json()
    ]
    assert client.get(f"/api/v1/models/{dm_id}", headers=world["viewer"].h).status_code == 404
    r = client.post(
        f"/api/v1/organisations/{world['org_id']}/engine-installations",
        headers=world["admin"].h,
        json={
            "engine_id": "virtualcrash",
            "label": "Reconstruction lab workstation",
            "edition": "Virtual CRASH (training record)",
            "licence_confirmed": True,
            "operator_contact": "reconstruction analyst",
        },
    )
    assert r.status_code == 201, r.text
    sc = client.post(
        f"/api/v1/projects/{pid}/scenarios",
        headers=inv,
        json={
            "domain_model_id": dm_id,
            "name": "H1 analysis",
            "content": {
                "discipline": "collision",
                "engine_id": "virtualcrash",
                "controls": {"kind": "collision", "hypothesis_id": hyps["H1"]},
            },
        },
    )
    assert sc.status_code == 201, sc.text
    run = client.post(
        f"/api/v1/projects/{pid}/runs", headers=inv, json={"scenario_revision_id": sc.json()["id"]}
    ).json()
    assert run["state"] == "awaiting_external_result" and run["case_id"] == cid
    assert client.get(f"/api/v1/runs/{run['id']}", headers=world["viewer"].h).status_code == 404
    listed = client.get(f"/api/v1/projects/{pid}/runs", headers=world["viewer"].h).json()["items"]
    assert run["id"] not in [x["id"] for x in listed]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "trajectories.csv",
            "veh,time,px,py,yaw,v\nCar A,0,-20,-1.5,0,50\nCar A,1.5,12.2,1.2,21,0\n"
            "Car B,0,6,-30,90,40\nCar B,1.5,6.1,-2.3,98,0\n",
        )
        z.writestr("metadata.json", json.dumps({"software_version": "training", "operator": "analyst"}))
    mapping = {
        "vehicle_column": "veh",
        "time_column": "time",
        "x_column": "px",
        "y_column": "py",
        "heading_column": "yaw",
        "speed_column": "v",
        "speed_unit": "km/h",
        "vehicle_id_map": {"Car A": vehicles["Vehicle 1"], "Car B": vehicles["Vehicle 2"]},
    }
    r = client.post(
        f"/api/v1/runs/{run['id']}/external-result",
        headers=inv,
        files={"file": ("result.zip", buf.getvalue(), "application/zip")},
        data={
            "external_installation": "Reconstruction lab workstation (Virtual CRASH)",
            "supplied_provenance": "Analyst exported the H1 simulation on 2026-10-22",
            "options": json.dumps({"column_mapping": mapping, "hypothesis_id": hyps["H1"]}),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "completed"
    a = client.post(f"/api/v1/hypotheses/{hyps['H1']}/analyses", headers=inv, json={"run_id": run["id"]})
    assert a.status_code == 201, a.text
    fit = a.json()["evidence_fit"]
    assert fit["rms_residual_m"] == pytest.approx(0.3, abs=1e-9) and fit["targets_within_uncertainty"] == 2
    assert (
        client.post(
            f"/api/v1/hypotheses/{hyps['H2']}/analyses", headers=inv, json={"run_id": run["id"]}
        ).status_code
        == 422
    )  # the run analysed H1
    cmp_ = client.get(f"/api/v1/cases/{cid}/hypotheses-comparison", headers=inv).json()
    by = {h["hypothesis_id"]: h for h in cmp_["hypotheses"]}
    assert by[hyps["H1"]]["rms_residual_m"] == pytest.approx(0.3) and by[hyps["H2"]]["rms_residual_m"] is None
    assert "does not compute probabilities" in cmp_["basis"]
    pb = client.get(f"/api/v1/hypotheses/{hyps['H1']}/playback", headers=inv).json()
    assert {s["label"] for s in pb["sources"]} == {"Imported external reconstruction"}
    analyses = client.get(f"/api/v1/cases/{cid}/external-analyses", headers=inv).json()
    assert analyses[0]["supplied_provenance"].startswith("Analyst exported") and analyses[0]["package_sha256"]
    # case runs go only into that case's report
    r = client.post(
        f"/api/v1/projects/{pid}/reports",
        headers=world["editor"].h,
        json={"kind": "project", "title": "x", "run_ids": [run["id"]]},
    )
    assert r.status_code in (404, 422)
    r = client.post(
        f"/api/v1/projects/{pid}/reports",
        headers=inv,
        json={"kind": "case", "title": "TC-001 case report", "case_id": cid, "run_ids": [run["id"]]},
    )
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["status"] == "completed", rep["error"]
    man = rep["manifest"]["case"]
    assert man["reference"] == "TC-001" and man["evidence"][0]["original_sha256"]
    assert {h["id"] for h in man["hypotheses"]} == set(hyps.values())
    url = client.get(f"/api/v1/reports/{rep['id']}/files/report.pdf", headers=inv).json()["url"]
    pdf = client.get(url.split("http://localhost:8000", 1)[1]).content
    if os.environ.get("PARALLEL_REPORT_DUMP"):  # visual inspection aid
        Path(os.environ["PARALLEL_REPORT_DUMP"]).write_bytes(pdf)
    txt = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages).replace("\n", " ")
    for needle in (
        "does not determine fault",
        "TC-001",
        "Training case",
        "Evidence references",
        hyps["H1"],
        ev,
        "Based on O1",
        "evidence_annotations",
    ):
        assert needle in txt, needle
    assert client.get(f"/api/v1/reports/{rep['id']}", headers=world["viewer"].h).status_code == 404
    assert rep["id"] not in [
        x["id"] for x in client.get(f"/api/v1/projects/{pid}/reports", headers=world["viewer"].h).json()
    ]
