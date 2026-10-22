"""Reconstruction pipeline end to end: synthetic capture -> COLMAP -> scale from surveyed positions -> scene."""

from __future__ import annotations

import csv
import io
import json

import numpy as np
import pytest


@pytest.fixture(scope="module")
def capture(tmp_path_factory):
    from parallel_reconstruction.synthetic import generate, road_scene

    out = tmp_path_factory.mktemp("capture")
    gt = generate(road_scene(), out, n_frames=36, width=560, height_px=420, video=False, blur_every=9)
    return out, gt


def _csv(rows, header):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def test_reconstruction_pipeline_scale_and_measurement_checks(client, world, capture):
    out, gt = capture
    ed = world["editor"].h
    p = client.post("/api/v1/projects", headers=ed, json={"organisation_id": world["org_id"], "name": "CrashLens scene",
                                                          "project_type": "crashlens"}).json()
    batch = client.post(f"/api/v1/projects/{p['id']}/capture-batches", headers=ed,
                        json={"name": "Synthetic training scene photos", "kind": "photos"}).json()
    for name in gt.frame_names:
        r = client.post(f"/api/v1/projects/{p['id']}/assets", headers=ed, data={"capture_batch_id": batch["id"]},
                        files={"file": (name, (out / "frames" / name).read_bytes(), "image/png")})
        assert r.status_code == 201, r.text
    cams = _csv([[n, *c] for n, c in zip(gt.frame_names, gt.camera_centres, strict=True)], ["frame", "x", "y", "z"])
    refs = _csv([[k, *v] for k, v in gt.targets.items()], ["name", "x", "y", "z"])
    for fname, data in (("camera_positions.csv", cams), ("reference_points.csv", refs)):
        assert client.post(f"/api/v1/projects/{p['id']}/assets", headers=ed, data={"capture_batch_id": batch["id"]},
                           files={"file": (fname, data, "text/csv")}).status_code == 201
    r = client.post(f"/api/v1/projects/{p['id']}/reconstructions", headers=ed, json={
        "capture_batch_id": batch["id"], "settings": {"scene_name": "Training road scene",
                                                      "reference_source": "synthetic surveyed camera positions",
                                                      "tolerance_abs_m": 0.10}})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "completed", json.dumps(job["stages"], indent=1, default=str)[:3000]
    st = {s["name"]: s for s in job["stages"]}
    assert st["reconstruction_training"]["status"] == "skipped"
    assert "GPU worker unavailable" in st["reconstruction_training"]["message"]
    # blurred frames were rejected by the Laplacian filter
    assert st["frame_filtering"]["data"]["rejected"] >= 3
    frames = client.get(f"/api/v1/reconstructions/{job['id']}/frames", headers=ed).json()["frames"]
    assert any("blurred" in (f["reason"] or "") for f in frames)
    pe = st["pose_estimation"]["data"]
    assert pe["registered_fraction"] > 0.8 and pe["mean_reprojection_error_px"] < 2.0
    so = st["scale_orientation"]["data"]
    assert so["scale_established"] and so["residual_rms_m"] < 0.1
    rev = client.get(f"/api/v1/scene-revisions/{job['result_scene_revision_id']}", headers=ed).json()
    content = rev["content"]
    q = {r["state"]: r["achieved"] for r in content["quality"]["records"]}
    assert q["visual_reconstruction_available"] and q["scale_established"]
    assert not q["model_reviewed"] and not q["model_calibrated"]  # never granted automatically
    # independent checks use held-out surveyed camera positions (never used for the alignment)
    assert so["check_points"] and so["check_rms_m"] < 0.25
    ms = content["measurements"]
    assert ms and all(m["purpose"] == "independent_check" and m["abs_error_m"] is not None for m in ms)
    for m in ms:
        a = next(c for c in so["check_points"] if c["frame"] in m["label"].split(" to ")[0])
        b = next(c for c in so["check_points"] if c["frame"] in m["label"].split(" to ")[1])
        truth = float(np.linalg.norm(np.array(a["reference"]) - np.array(b["reference"])))
        assert abs(m["reference_distance"]["value"] - truth) < 1e-9
        assert m["abs_error_m"] < 0.25
    # the quality state follows the project tolerance (0.10 m here), never granted unconditionally
    assert q["measurements_checked"] == all(m["within_tolerance"] for m in ms)
    assert {v["kind"] for v in content["visual_assets"]} == {"point_cloud", "gaussian_initialisation"}
    assert any("Untrained" in n for v in content["visual_assets"] for n in v["quality_notes"])
    assert content["provenance"]["kind"] == "algorithm_estimate"
    # resume from scale_orientation re-runs only the later stages from checkpoints
    r = client.post(f"/api/v1/reconstructions/{job['id']}/resume?from_stage=scale_orientation", headers=ed).json()
    assert r["status"] == "completed"
    st2 = {s["name"]: s for s in r["stages"]}
    assert st2["pose_estimation"]["data"] == pe  # checkpointed stage not re-run
    revs = client.get(f"/api/v1/scenes/{rev['scene_id']}/revisions", headers=ed).json()
    assert len(revs) == 2
