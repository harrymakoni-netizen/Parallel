"""Training a splat outside Parallel: the training package, the import with its alignment check, and the
notebook helpers that run on the GPU. Uses a real synthetic reconstruction (COLMAP) like test_reconstruction."""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[3]


def _tools():
    spec = importlib.util.spec_from_file_location("splat_tools", REPO / "scripts" / "training_notebook" / "splat_tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tools = _tools()


@pytest.fixture(scope="module")
def reconstruction(client, world, tmp_path_factory):
    from parallel_reconstruction.synthetic import generate, road_scene

    out = tmp_path_factory.mktemp("capture")
    gt = generate(road_scene(), out, n_frames=30, width=480, height_px=360, video=False)
    ed, admin = world["editor"].h, world["admin"]
    p = client.post("/api/v1/projects", headers=ed, json={"organisation_id": world["org_id"], "name": "External training",
                                                          "project_type": "crashlens"}).json()
    client.post(f"/api/v1/projects/{p['id']}/members", headers=admin.h, json={"user_id": world["viewer"].id, "role": "viewer"})
    batch = client.post(f"/api/v1/projects/{p['id']}/capture-batches", headers=ed,
                        json={"name": "Photos", "kind": "photos"}).json()
    for name in gt.frame_names:
        r = client.post(f"/api/v1/projects/{p['id']}/assets", headers=ed, data={"capture_batch_id": batch["id"]},
                        files={"file": (name, (out / "frames" / name).read_bytes(), "image/png")})
        assert r.status_code == 201, r.text
    r = client.post(f"/api/v1/projects/{p['id']}/reconstructions", headers=ed,
                    json={"capture_batch_id": batch["id"], "settings": {"scene_name": "Training scene"}})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "completed", job["error"]
    return {"project": p, "job": job}


def _download(client, asset_id: str, headers) -> bytes:
    url = client.get(f"/api/v1/assets/{asset_id}/url", headers=headers).json()["url"]
    return client.get(url.split("http://localhost:8000", 1)[1]).content


def _sparse(job) -> np.ndarray:
    from parallel_core.storage import get_storage

    key = next(s for s in job["stages"] if s["name"] == "pose_estimation")["outputs"]["colmap/sparse_txt/points3D.txt"]
    rows = [ln.split()[1:4] for ln in get_storage().get_bytes(key).decode().splitlines() if ln and not ln.startswith("#")]
    return np.array(rows, dtype=float)


def _splat(sfm: np.ndarray, *, n=20000, sh=True, jitter=0.002, shift=(0, 0, 0), scale=1.0, seed=1) -> bytes:
    rng = np.random.default_rng(seed)
    spread = np.median(np.linalg.norm(sfm - np.median(sfm, axis=0), axis=1))
    pts = sfm[rng.integers(0, len(sfm), n)] + rng.normal(0, jitter * spread, (n, 3))
    pts = pts * scale + np.array(shift) * spread
    names = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2"]
    if sh:
        names += [f"f_rest_{i}" for i in range(45)]
    names += ["opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    arr = np.zeros(n, dtype=[(k, "<f4") for k in names])
    arr["x"], arr["y"], arr["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
    arr["opacity"] = rng.normal(2.0, 0.5, n)
    for i in range(3):
        arr[f"scale_{i}"] = np.log(0.01 * spread)
        arr[f"f_dc_{i}"] = rng.normal(0, 0.5, n)
    arr["rot_0"] = 1.0
    buf = io.BytesIO()
    tmp = Path(f"/tmp/_splat_{seed}_{n}.ply")
    tools.write_ply(tmp, arr, ["test splat"])
    buf.write(tmp.read_bytes())
    tmp.unlink()
    return buf.getvalue()


def _import(client, headers, job_id, splat: bytes, info: dict | None = None, filename="splat.ply"):
    files = {"splat": (filename, splat, "application/octet-stream")}
    if info is not None:
        files["training_info"] = ("training_info.json", json.dumps(info).encode(), "application/json")
    return client.post(f"/api/v1/reconstructions/{job_id}/import-splat", headers=headers, files=files)


def _package(client, world, rec):
    r = client.post(f"/api/v1/reconstructions/{rec['job']['id']}/training-package", headers=world["editor"].h)
    assert r.status_code == 201, r.text
    return r.json()


def test_training_package_contents_and_idempotence(client, world, reconstruction):
    asset = _package(client, world, reconstruction)
    assert asset["kind"] == "archive" and asset["inspection"]["frames"] >= 20
    again = client.post(f"/api/v1/reconstructions/{reconstruction['job']['id']}/training-package", headers=world["editor"].h)
    assert again.json()["id"] == asset["id"]  # the same stored, hashed package is reused
    z = zipfile.ZipFile(io.BytesIO(_download(client, asset["id"], world["editor"].h)))
    names = set(z.namelist())
    assert {"manifest.json", "README.txt", "colmap/sparse/0/cameras.txt", "colmap/sparse/0/images.txt",
            "colmap/sparse/0/points3D.txt"} <= names
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["format"] == "parallel-training-package/1" and manifest["job_id"] == reconstruction["job"]["id"]
    images = {n.split("/", 1)[1] for n in names if n.startswith("images/")}
    reg = [ln.split()[9] for ln in z.read("colmap/sparse/0/images.txt").decode().splitlines()
           if ln and not ln.startswith("#") and len(ln.split()) >= 10 and ln.split()[9].endswith(".png")]
    assert reg and set(reg) <= images  # every registered frame is in the package
    import hashlib

    for f in manifest["files"]:
        assert hashlib.sha256(z.read(f["path"])).hexdigest() == f["sha256"]
    assert asset["inspection"]["manifest_sha256"] == hashlib.sha256(z.read("manifest.json")).hexdigest()


def test_training_package_requires_edit_access(client, world, reconstruction):
    jid = reconstruction["job"]["id"]
    assert client.post(f"/api/v1/reconstructions/{jid}/training-package", headers=world["viewer"].h).status_code == 403
    assert client.post(f"/api/v1/reconstructions/{jid}/training-package", headers=world["outsider"].h).status_code in (403, 404)
    assert client.post(f"/api/v1/reconstructions/{jid}/training-package").status_code == 401


def test_import_splat_adds_a_revision_with_provenance(client, world, reconstruction):
    ed = world["editor"].h
    job = reconstruction["job"]
    package = _package(client, world, reconstruction)
    before = client.get(f"/api/v1/scene-revisions/{job['result_scene_revision_id']}", headers=ed).json()["content"]
    info = {"job_id": job["id"], "package_manifest_sha256": package["inspection"]["manifest_sha256"],
            "trainer": "nerfstudio splatfacto", "trainer_version": "1.1.5", "gpu": "Tesla T4", "iterations": 15000,
            "notes": "test run", "unexpected": "ignored"}
    r = _import(client, ed, job["id"], _splat(_sparse(job)), info)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["gaussians"] == 20000 and out["alignment_check"]["passed"]
    assert out["training"]["verified_package"] == "yes" and "unexpected" not in out["training"]
    rev = client.get(f"/api/v1/scene-revisions/{out['scene_revision_id']}", headers=ed).json()
    content = rev["content"]
    new = next(v for v in content["visual_assets"] if v["id"] == out["visual_id"])
    assert new["kind"] == "gaussian_splat" and new["frame"] == "capture" and new["generated"] is False
    assert new["provenance"]["kind"] == "imported" and new["provenance"]["source"] == "nerfstudio splatfacto"
    assert new["provenance"]["recorded_by"] == world["editor"].id and new["provenance"]["derived_from"] == [package["id"]]
    assert any("Trained outside Parallel" in n for n in new["quality_notes"])
    assert any("not solid or walkable geometry" in n for n in new["quality_notes"])
    # the earlier visuals stay, the earlier revision is untouched, and no quality state is granted by importing
    assert {v["id"] for v in before["visual_assets"]} <= {v["id"] for v in content["visual_assets"]}
    again = client.get(f"/api/v1/scene-revisions/{job['result_scene_revision_id']}", headers=ed).json()["content"]
    assert again == before
    assert content["quality"] == before["quality"] and content["coordinates"] == before["coordinates"]
    assert rev["parent_revision_id"] == job["result_scene_revision_id"]
    asset = client.get(f"/api/v1/assets/{out['asset_id']}", headers=ed).json()
    assert asset["kind"] == "gaussian" and asset["derived_from_id"] == package["id"]
    # an audit event records the import with the alignment check and the training details
    from sqlalchemy import select

    from parallel_core.db import session_scope
    from parallel_core.models import AuditEvent

    with session_scope() as db:
        ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "scene.splat_imported",
                                                AuditEvent.object_id == out["scene_revision_id"]))
        assert ev is not None and ev.actor_id == world["editor"].id
        assert ev.details["alignment"]["passed"] and ev.details["training"]["gpu"] == "Tesla T4"
        assert ev.details["sha256"] == asset["sha256"]
    # the same file cannot be added twice
    dup = _import(client, ed, job["id"], _splat(_sparse(job)), info)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "already_imported"


def test_import_without_training_information_is_recorded_as_such(client, world, reconstruction):
    job = reconstruction["job"]
    r = _import(client, world["editor"].h, job["id"], _splat(_sparse(job), seed=7, n=15000))
    assert r.status_code == 201, r.text
    rev = client.get(f"/api/v1/scene-revisions/{r.json()['scene_revision_id']}", headers=world["editor"].h).json()
    new = next(v for v in rev["content"]["visual_assets"] if v["id"] == r.json()["visual_id"])
    assert new["provenance"]["note"] == "No training information was supplied"
    assert "not stated" in new["quality_notes"][0]


def test_misaligned_splats_are_refused_with_the_measurements(client, world, reconstruction):
    ed, job = world["editor"].h, reconstruction["job"]
    sfm = _sparse(job)
    for kwargs in ({"shift": (3, 0, 0)}, {"scale": 10.0}, {"scale": 0.1}):
        r = _import(client, ed, job["id"], _splat(sfm, seed=11, n=8000, **kwargs))
        assert r.status_code == 422, (kwargs, r.text)
        err = r.json()["error"]
        assert err["code"] == "splat_not_aligned" and err["details"]["passed"] is False
        assert "unchanged training package" in err["message"]


def test_malformed_splats_are_refused(client, world, reconstruction):
    ed, job = world["editor"].h, reconstruction["job"]
    good = _splat(_sparse(job), seed=21, n=5000)
    assert _import(client, ed, job["id"], good[:-100]).json()["error"]["code"] == "splat_invalid"  # truncated
    assert _import(client, ed, job["id"], b"not a ply file").status_code == 422
    assert _import(client, ed, job["id"], good, filename="splat.splat").json()["error"]["code"] == "splat_invalid"
    from parallel_scene import ply

    cloud = io.BytesIO()
    p = Path("/tmp/_cloud.ply")
    ply.write_point_cloud(p, _sparse(job), None)
    cloud.write(p.read_bytes())
    p.unlink()
    r = _import(client, ed, job["id"], cloud.getvalue())
    assert r.status_code == 422 and "not a Gaussian splat" in r.json()["error"]["message"]
    nan = bytearray(good)
    header_end = good.index(b"end_header\n") + len(b"end_header\n")
    nan[header_end:header_end + 4] = np.float32("nan").tobytes()
    assert "non-finite" in _import(client, ed, job["id"], bytes(nan)).json()["error"]["message"]


def test_import_checks_training_information_against_the_package(client, world, reconstruction):
    ed, job = world["editor"].h, reconstruction["job"]
    package = _package(client, world, reconstruction)
    splat = _splat(_sparse(job), seed=31, n=6000)
    wrong_job = _import(client, ed, job["id"], splat, {"job_id": "someone-elses-job"})
    assert wrong_job.status_code == 422 and wrong_job.json()["error"]["code"] == "package_mismatch"
    wrong_pkg = _import(client, ed, job["id"], splat, {"job_id": job["id"], "package_manifest_sha256": "0" * 64})
    assert wrong_pkg.status_code == 422 and wrong_pkg.json()["error"]["code"] == "package_mismatch"
    bad_json = client.post(f"/api/v1/reconstructions/{job['id']}/import-splat", headers=ed, files={
        "splat": ("s.ply", splat, "application/octet-stream"), "training_info": ("i.json", b"{nope", "application/json")})
    assert bad_json.json()["error"]["code"] == "training_info_invalid"
    ok = _import(client, ed, job["id"], splat, {"job_id": job["id"], "package_manifest_sha256":
                                                 package["inspection"]["manifest_sha256"]})
    assert ok.status_code == 201, ok.text


def test_import_requires_edit_access(client, world, reconstruction):
    job = reconstruction["job"]
    splat = _splat(_sparse(job), seed=41, n=5000)
    assert _import(client, world["viewer"].h, job["id"], splat).status_code == 403
    assert _import(client, world["outsider"].h, job["id"], splat).status_code in (403, 404)
    assert client.post(f"/api/v1/reconstructions/{job['id']}/import-splat",
                       files={"splat": ("s.ply", splat, "application/octet-stream")}).status_code == 401


def test_notebook_is_served_current_and_valid(client, world):
    assert client.get("/api/v1/external-training/notebook").status_code == 401
    r = client.get("/api/v1/external-training/notebook", headers=world["viewer"].h)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    nb = r.json()
    assert nb["nbformat"] == 4
    import ast

    for cell in nb["cells"]:
        if cell["cell_type"] == "code":
            ast.parse("".join(ln for ln in cell["source"] if not ln.lstrip().startswith(("%", "!"))))
    text = "".join("".join(c["source"]) for c in nb["cells"])
    assert "--orientation-method" in text and "none" in text and "def alignment_check" in text
    import subprocess
    import sys

    assert subprocess.run([sys.executable, str(REPO / "scripts" / "training_notebook" / "build.py"), "--check"]).returncode == 0, \
        "the committed notebook is stale: run scripts/training_notebook/build.py"


def test_notebook_check_matches_the_servers(reconstruction):
    from parallel_scene import splat as server

    sfm = _sparse(reconstruction["job"])
    for kwargs in ({}, {"shift": (3, 0, 0)}, {"scale": 5.0}):
        data = _splat(sfm, seed=51, n=6000, **kwargs)
        p = Path("/tmp/_cmp.ply")
        p.write_bytes(data)
        info, xyz = server.validate(p)
        d, _ = tools.read_ply(p)
        p.unlink()
        a, b = server.alignment_check(xyz, sfm), tools.alignment_check(tools.positions(d), sfm)
        assert a["passed"] == b["passed"]
        assert a["median_distance_fraction_of_spread"] == pytest.approx(b["median_distance_fraction_of_spread"], rel=1e-3)
        assert info.sh_degree == 3 and info.count == 6000


def test_notebook_converts_a_changed_frame_back():
    """If nerfstudio re-orients, centres or scales the data, the notebook undoes it before upload."""
    from scipy.spatial.transform import Rotation

    rng = np.random.default_rng(3)
    n = 500
    names = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "f_rest_0", "opacity", "scale_0", "scale_1", "scale_2",
             "rot_0", "rot_1", "rot_2", "rot_3"]
    cap = np.zeros(n, dtype=[(k, "<f4") for k in names])
    xyz = rng.normal(0, 3, (n, 3))
    cap["x"], cap["y"], cap["z"] = xyz.T
    for i in range(3):
        cap[f"scale_{i}"] = rng.normal(-3, 0.2, n)
    q = Rotation.random(n, random_state=4).as_quat()  # xyzw
    for i, c in enumerate((3, 0, 1, 2)):
        cap[f"rot_{i}"] = q[:, c]
    R = Rotation.random(random_state=5).as_matrix()
    t, s = np.array([1.5, -2.0, 0.7]), 0.37
    train = cap.copy()
    fwd = s * (xyz @ R.T + t)
    train["x"], train["y"], train["z"] = fwd.T
    for i in range(3):
        train[f"scale_{i}"] = cap[f"scale_{i}"] + np.log(s)
    qt = (Rotation.from_matrix(R) * Rotation.from_quat(q)).as_quat()
    for i, c in enumerate((3, 0, 1, 2)):
        train[f"rot_{i}"] = qt[:, c]
    back = tools.to_capture_frame(train, R, t, s)
    assert "f_rest_0" not in back.dtype.names  # view-dependent colour cannot be rotated here, so it is dropped
    np.testing.assert_allclose(tools.positions(back), xyz, atol=1e-4)
    np.testing.assert_allclose(back["scale_0"], cap["scale_0"], atol=1e-5)
    qb = np.stack([back[f"rot_{i}"] for i in range(4)], axis=1)
    want = np.stack([cap[f"rot_{i}"] for i in range(4)], axis=1)
    np.testing.assert_allclose(np.abs((qb * want).sum(axis=1)), 1.0, atol=1e-4)  # same rotation (q = -q)
    assert tools.is_identity(np.eye(3), np.zeros(3), 1.0) and not tools.is_identity(R, t, s)


def test_notebook_detects_a_tampered_package(tmp_path, client, world, reconstruction):
    asset = _package(client, world, reconstruction)
    z = zipfile.ZipFile(io.BytesIO(_download(client, asset["id"], world["editor"].h)))
    z.extractall(tmp_path)
    assert tools.verify_package(tmp_path)["manifest_sha256"] == asset["inspection"]["manifest_sha256"]
    victim = next(p for p in (tmp_path / "images").iterdir())
    victim.write_bytes(victim.read_bytes() + b"x")
    with pytest.raises(ValueError, match="missing or changed"):
        tools.verify_package(tmp_path)
