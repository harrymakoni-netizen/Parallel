"""Demonstration package: real recorded runs labelled as saved results, and a reset that touches only
demonstration projects."""

from __future__ import annotations

from sqlalchemy import select


def test_seed_and_reset_only_touch_demonstration_projects(client, world, project):
    from parallel_api import demo
    from parallel_api.routers.scenes import quality_view
    from parallel_contracts.scene import SceneRevisionContent
    from parallel_core.db import session_scope
    from parallel_core.models import Project, Report, Scene, SceneRevision, SimulationRun, SourceAsset, User
    from parallel_core.storage import get_storage

    res = demo.seed(only=["urban_drainage"])
    assert "project_id" in res["urban_drainage"], res
    pid = res["urban_drainage"]["project_id"]
    with session_scope() as db:
        p = db.get(Project, pid)
        assert p.demo_key == "urban_drainage"
        runs = db.scalars(select(SimulationRun).where(SimulationRun.project_id == pid)).all()
        assert len(runs) == 3 and all(
            r.state == "completed" and r.label.startswith("Saved result") for r in runs
        )
        assert len({r.result_summary["rainfall_hash"] for r in runs}) == 1  # same storm in every scenario
        assert db.scalar(select(Report.status).where(Report.project_id == pid)) == "completed"
        # downstream quality states are read from the model and runs that cite the scene revision
        sc = db.scalars(select(Scene).where(Scene.project_id == pid)).one()
        rev = db.get(SceneRevision, sc.current_revision_id)
        presenter = db.scalar(select(User).where(User.email == "presenter@parallel-demo.example"))
        q = {r["state"]: r for r in quality_view(db, presenter, rev, sc, SceneRevisionContent.model_validate(rev.content))}
        assert q["domain_model_prepared"]["achieved"] and q["domain_model_prepared"]["source"] == "derived"
        assert q["simulation_integration_verified"]["achieved"], q["simulation_integration_verified"]
        assert not q["model_reviewed"]["achieved"] and not q["model_calibrated"]["achieved"]
        assert not q["visual_reconstruction_available"]["achieved"]  # a plan, not a capture
        docs = {a.filename for a in db.scalars(select(SourceAsset).where(SourceAsset.project_id == pid))}
        assert {"source-information.md", "presenter-notes.md"} <= docs
        keys = [a.storage_key for a in db.scalars(select(SourceAsset).where(SourceAsset.project_id == pid))]
    assert demo.seed(only=["urban_drainage"])["urban_drainage"] == "exists"  # idempotent
    counts = demo.reset()
    assert counts["projects"] == 1 and counts["files"] > 0
    with session_scope() as db:
        assert db.get(Project, pid) is None
        assert db.get(Project, project) is not None  # unrelated projects are untouched
    assert not any(get_storage().exists(k) for k in keys)
