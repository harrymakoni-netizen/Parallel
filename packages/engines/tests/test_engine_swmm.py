"""SWMM reference workflow: real execution, normalised volumes cross-checked with the SWMM report."""

import math

import pytest

from parallel_contracts.domain.drainage import ConduitBlockage, NodeStorageConversion
from parallel_engines.adapters.swmm import filled_circular_area_fraction, parse_report
from parallel_engines.reference_models import drainage_model, drainage_scenario

from .conftest import metric, require_engine, run_managed


@pytest.fixture(autouse=True)
def _engine():
    require_engine("swmm")


def test_baseline_volumes_agree_with_native_report(workdir):
    _, execution, result, _ = run_managed(
        "swmm", drainage_model(), drainage_scenario(is_baseline=True), workdir
    )
    assert execution.engine_version.startswith("5.2")
    assert result.crosschecks and all(c["passed"] for c in result.crosschecks), result.crosschecks
    # 9.5 ha of subcatchments x the storm depth; the report rounds to 0.001 ha-m (10 m³)
    depth_m = drainage_model().content.rainfall[0].total_depth_mm / 1000
    assert math.isclose(metric(result, "drainage.total_rainfall_volume").value, 9.5e4 * depth_m, abs_tol=10.0)
    assert abs(metric(result, "drainage.routing_continuity_error").value) < 1.0
    assert metric(result, "drainage.total_flooding_volume").value > 0


def test_blockage_increases_flooding_and_storage_reduces_it(workdir):
    base = run_managed("swmm", drainage_model(), drainage_scenario(), workdir / "a")[2]
    blocked_prep, _, blocked, _ = run_managed(
        "swmm",
        drainage_model(),
        drainage_scenario(blockages=[ConduitBlockage(conduit_id="C3", sediment_depth_fraction=0.5)]),
        workdir / "b",
    )
    stored = run_managed(
        "swmm",
        drainage_model(),
        drainage_scenario(
            storage_conversions=[NodeStorageConversion(junction_id="J2", area_m2=600, max_depth_m=2.2)]
        ),
        workdir / "c",
    )[2]
    fb, fx, fs = (metric(r, "drainage.total_flooding_volume").value for r in (base, blocked, stored))
    assert fx > fb > fs
    assert any("FILLED_CIRCULAR" in n for n in blocked_prep.model_change_notes)
    for r in (blocked, stored):
        assert all(c["passed"] for c in r.crosschecks)


def test_same_rainfall_hash_across_infrastructure_scenarios(workdir):
    a = run_managed("swmm", drainage_model(), drainage_scenario(), workdir / "a")[0]
    b = run_managed(
        "swmm",
        drainage_model(),
        drainage_scenario(resizes=[{"conduit_id": "C2", "diameter_m": 0.75}]),
        workdir / "b",
    )[0]
    assert a.summary["rainfall_hash"] == b.summary["rainfall_hash"]


def test_filled_circular_area_fraction():
    assert math.isclose(filled_circular_area_fraction(0.5), 0.5)
    assert filled_circular_area_fraction(0.25) < 0.25


def test_report_parser_on_native_fixture():
    text = """
  **************************        Volume         Depth
  Runoff Quantity Continuity     hectare-m            mm
  **************************     ---------       -------
  Total Precipitation ......         0.428        45.000
  Surface Runoff ...........         0.345        36.287
  Continuity Error (%) .....        -0.025


  **************************        Volume        Volume
  Flow Routing Continuity        hectare-m      10^6 ltr
  **************************     ---------     ---------
  Flooding Loss ............         0.121         1.210
  Continuity Error (%) .....        -0.060
"""
    r = parse_report(text)
    assert r["precipitation_ha_m"] == 0.428 and r["flooding_loss_ha_m"] == 0.121
    assert r["runoff_continuity_pct"] == -0.025 and r["routing_continuity_pct"] == -0.06


def test_disconnected_node_rejected():
    m = drainage_model().model_dump()
    m["content"]["junctions"].append({"id": "J9", "position": [0, 0], "invert_elev_m": 99, "max_depth_m": 1})
    from parallel_contracts.domain import DomainModelEnvelope
    from parallel_engines.registry import get_adapter

    report = get_adapter("swmm").validate_model(DomainModelEnvelope.model_validate(m))
    assert not report.ok and any(i.code == "disconnected_node" for i in report.errors)
