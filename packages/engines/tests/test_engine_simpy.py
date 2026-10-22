"""SimPy reference workflow: resource conservation and event-log agreement."""

import pytest

from parallel_engines.reference_models import operations_scenario, site_operations_model

from .conftest import metric, require_engine, run_managed


@pytest.fixture(autouse=True)
def _engine():
    require_engine("simpy")


def test_conservation_and_completion_agree_with_events(workdir):
    _, execution, result, _ = run_managed(
        "simpy", site_operations_model(), operations_scenario(is_baseline=True), workdir
    )
    assert execution.engine_version == "4.1.2"
    assert all(c["passed"] for c in result.crosschecks)
    for r in ("gate", "bay", "crane"):
        assert 0 <= metric(result, "ops.utilisation", r).value <= 1


def test_second_loading_bay_with_common_random_numbers(workdir):
    base = run_managed("simpy", site_operations_model(), operations_scenario(), workdir / "a")[2]
    alt = run_managed(
        "simpy", site_operations_model(), operations_scenario(capacity_changes={"bay": 2}), workdir / "b"
    )[2]
    # The gate sees identical arrivals and service times in both scenarios (matched seeds)
    assert metric(alt, "ops.mean_wait", "gate").value == metric(base, "ops.mean_wait", "gate").value
    assert metric(alt, "ops.mean_wait", "bay").value < metric(base, "ops.mean_wait", "bay").value
