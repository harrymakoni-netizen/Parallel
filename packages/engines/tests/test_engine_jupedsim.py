"""JuPedSim reference workflow: real execution in the isolated runtime."""

from __future__ import annotations

import pytest

from parallel_engines.reference_models import mine_section_model, pedestrian_scenario, premovement
from parallel_engines.registry import get_adapter

from .conftest import metric, require_engine, run_managed


@pytest.fixture(autouse=True)
def _engine():
    require_engine("jupedsim")


def test_baseline_everyone_evacuates_and_accounting_holds(workdir):
    model = mine_section_model()
    sc = pedestrian_scenario(is_baseline=True, max_duration_s=300, pre_movement=premovement(5, 15))
    prepared, execution, result, monitor = run_managed("jupedsim", model, sc, workdir)
    assert execution.engine_version == "1.4.2"
    n = metric(result, "evac.initial_population").value
    ev, un, rem = (metric(result, f"evac.{k}").value for k in ("evacuated", "unreachable", "remaining"))
    assert n == 60 and ev + un + rem == n
    assert ev == 60 and un == 0 and rem == 0
    t50, t95, t100 = (metric(result, f"evac.t{p}").value for p in (50, 95, 100))
    assert 0 < t50 <= t95 <= t100 < 300
    # Everyone started moving after the pre-movement window
    assert t50 > 5
    assert all(cc["passed"] for cc in result.crosschecks), result.crosschecks
    usage = {m.entity: m.value for m in result.metrics if m.metric_id == "evac.exit_usage"}
    assert sum(usage.values()) == 60 and set(usage) == {"west_portal", "east_portal", "refuge"}
    assert any(p is not None for p, _ in monitor.messages)  # heartbeats with progress were emitted
    assert (workdir / "normalised" / "trajectories.parquet").exists()


def test_closures_make_people_unreachable_and_t100_unattained(workdir):
    model = mine_section_model()
    sc = pedestrian_scenario(
        closed_route_segments=["west_drift", "east_drift"], closed_exits=["refuge"], max_duration_s=300
    )
    prepared, _, result, _ = run_managed("jupedsim", model, sc, workdir)
    # Face crew (between the closed drifts) and the development crew (crosscut, refuge closed) cannot reach a portal.
    assert metric(result, "evac.unreachable").value == 40
    assert metric(result, "evac.evacuated").value == 20
    assert metric(result, "evac.remaining").value == 0
    t100 = metric(result, "evac.t100")
    assert t100.value is None and t100.status.value == "unattained"
    assert metric(result, "evac.t50").value is None  # only 20 of 60 evacuated
    assert any("unreachable" in w for w in result.warnings)
    assert any("closed from t = 0" in n for n in prepared.model_change_notes)


def test_short_horizon_leaves_people_remaining_without_faking_clearance(workdir):
    model = mine_section_model()
    sc = pedestrian_scenario(max_duration_s=12, pre_movement=premovement(0, 2))
    _, _, result, _ = run_managed("jupedsim", model, sc, workdir)
    rem = metric(result, "evac.remaining").value
    assert rem > 0
    assert metric(result, "evac.t100").value is None
    assert metric(result, "evac.evacuated").value + rem == 60


def test_matched_populations_across_scenarios(workdir):
    model = mine_section_model()
    a = get_adapter("jupedsim")
    from parallel_engines.base import RunContext, RunWorkspace

    hashes = []
    for i, sc in enumerate(
        [pedestrian_scenario(), pedestrian_scenario(closed_route_segments=["east_drift"])]
    ):
        ws = RunWorkspace(workdir / f"s{i}").create()
        prep = a.prepare_inputs(RunContext("r", ws, model, sc, seed=sc.settings.seed))
        hashes.append(prep.summary["population_positions_sha256"])
    assert hashes[0] == hashes[1]


def test_validation_rejects_self_intersecting_walkable_area():
    from parallel_contracts.domain import DomainModelEnvelope

    model = mine_section_model().model_dump()
    model["content"]["walkable_area"] = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]],
    }
    report = get_adapter("jupedsim").validate_model(DomainModelEnvelope.model_validate(model))
    assert not report.ok and report.errors[0].code == "self_intersection"


def test_unknown_closure_rejected():
    report = get_adapter("jupedsim").validate_scenario(
        mine_section_model(), pedestrian_scenario(closed_exits=["nope"])
    )
    assert not report.ok
