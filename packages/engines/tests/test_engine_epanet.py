"""EPANET reference workflow (WNTR + EPANET 2.2 toolkit)."""

import math

import pytest

from parallel_engines.reference_models import water_network_model, water_scenario

from .conftest import metric, require_engine, run_managed


@pytest.fixture(autouse=True)
def _engine():
    require_engine("epanet")


def test_baseline_mass_balance_and_demand(workdir):
    _, _, result, _ = run_managed("epanet", water_network_model(), water_scenario(is_baseline=True), workdir)
    assert all(c["passed"] for c in result.crosschecks)
    # 26 L/s base demand x pattern multipliers summing to 24.7 over the 24 hourly steps
    assert math.isclose(metric(result, "water.total_demand").value, 26e-3 * 24.7 * 3600, rel_tol=1e-4)
    assert metric(result, "water.network_min_pressure").value > 15


def test_constant_fire_flow_adds_exact_volume(workdir):
    base = run_managed("epanet", water_network_model(), water_scenario(), workdir / "a")[2]
    fire = run_managed(
        "epanet", water_network_model(), water_scenario(extra_demand_lps={"J6": 15}), workdir / "b"
    )[2]
    extra = metric(fire, "water.total_demand").value - metric(base, "water.total_demand").value
    assert math.isclose(extra, 15e-3 * 86400, rel_tol=1e-4)
    assert metric(fire, "water.min_pressure", "J6").value < metric(base, "water.min_pressure", "J6").value
