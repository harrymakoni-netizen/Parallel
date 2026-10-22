"""SUMO reference workflow: real execution in the isolated runtime (TraCI)."""

import pytest

from parallel_engines.reference_models import crossroads_model, traffic_scenario

from .conftest import metric, require_engine, run_managed


@pytest.fixture(autouse=True)
def _engine():
    require_engine("sumo")


def test_baseline_accounts_for_all_demand(workdir):
    _, execution, result, _ = run_managed(
        "sumo", crossroads_model(), traffic_scenario(is_baseline=True), workdir
    )
    assert execution.engine_version == "1.28.0"
    assert metric(result, "traffic.scheduled_trips").value == 450
    done = metric(result, "traffic.completed_trips").value
    assert done + metric(result, "traffic.unfinished_trips").value == 450
    assert all(c["passed"] for c in result.crosschecks), result.crosschecks
    assert metric(result, "traffic.mean_travel_time").value > 0
    assert metric(result, "traffic.teleports").value == 0


def test_closure_reroutes_via_bypass_and_increases_travel_time(workdir):
    base = run_managed("sumo", crossroads_model(), traffic_scenario(), workdir / "a")[2]
    closed = run_managed(
        "sumo", crossroads_model(), traffic_scenario(closed_edges=["C_E", "E_C"]), workdir / "b"
    )[2]
    assert (
        metric(closed, "traffic.completed_trips").value
        == metric(base, "traffic.completed_trips").value
        == 450
    )
    assert metric(closed, "traffic.mean_travel_time").value > metric(base, "traffic.mean_travel_time").value


def test_destination_closure_reports_unfinished_demand_not_better_travel_time(workdir):
    prepared, _, result, _ = run_managed(
        "sumo", crossroads_model(), traffic_scenario(closed_edges=["E_E0"]), workdir
    )
    assert metric(result, "traffic.unfinished_trips").value >= 150
    assert metric(result, "traffic.not_inserted").value >= 150
    assert metric(result, "traffic.completion_ratio").value < 0.7
    assert any("unfinished" in w for w in prepared.warnings)
    assert all(c["passed"] for c in result.crosschecks)


def test_signalised_alternative_runs(workdir):
    _, _, result, _ = run_managed(
        "sumo", crossroads_model(), traffic_scenario(node_type_changes={"C": "traffic_light"}), workdir
    )
    assert metric(result, "traffic.completed_trips").value > 0


def test_same_seed_is_reproducible(workdir):
    a = run_managed("sumo", crossroads_model(), traffic_scenario(seed=5), workdir / "a")[2]
    b = run_managed("sumo", crossroads_model(), traffic_scenario(seed=5), workdir / "b")[2]
    assert metric(a, "traffic.mean_travel_time").value == metric(b, "traffic.mean_travel_time").value
