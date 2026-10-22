"""File-exchange integrations: packages, safe archive handling and result imports.

Imports here use Parallel-authored fixtures that follow each documented template.
They verify Parallel's side of the exchange, NOT compatibility with vendor output.
"""

import json
import zipfile

import numpy as np
import pytest

from parallel_contracts.engines import Availability
from parallel_contracts.scenario import ScenarioContent
from parallel_engines.adapters.fds import read_fds_csv, write_fds
from parallel_engines.adapters.isaacsim import evaluate_reference
from parallel_engines.base import EngineError, RunContext, RunWorkspace
from parallel_engines.exchange import UnsafeArchive, safe_extract
from parallel_engines.reference_models import (
    collision_case_model,
    excavation_continuum_model,
    mine_ventilation_model,
    room_fire_model,
)
from parallel_engines.registry import all_adapters, get_adapter


def _ctx(tmp, model, controls, engine):
    sc = ScenarioContent(discipline=model.discipline, engine_id=engine, controls=controls)
    return RunContext("run-x", RunWorkspace(tmp).create(), model, sc, seed=None)


def test_every_catalogue_engine_has_an_adapter_and_complete_capability():
    ids = {a.engine_id for a in all_adapters()}
    assert ids == {
        "jupedsim",
        "isaacsim",
        "sumo",
        "fds",
        "swmm",
        "epanet",
        "hecras",
        "simpy",
        "ventsim",
        "openfoam",
        "flac3d",
        "3dec",
        "energyplus",
        "virtualcrash",
        "pccrash",
    }
    for a in all_adapters():
        c = a.capability
        assert c.disciplines and c.execution_modes and c.limitations and c.tested_versions and c.licence
        check = a.check_installation()
        assert check.availability in Availability
        # A licensed engine is never "ready" without a registered installation
        if c.licence.requires_key:
            assert check.availability != Availability.ready


def test_safe_extract_rejects_traversal_and_symlinks(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.txt", "x")
    with pytest.raises(UnsafeArchive):
        safe_extract(bad, tmp_path / "out")
    link = tmp_path / "link.zip"
    with zipfile.ZipFile(link, "w") as z:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        z.writestr(info, "/etc/passwd")
    with pytest.raises(UnsafeArchive):
        safe_extract(link, tmp_path / "out2")
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.bin", b"\0" * 50_000_000)
    with pytest.raises(UnsafeArchive):
        safe_extract(bomb, tmp_path / "out3")


def test_fds_input_and_csv_import(tmp_path):
    model = room_fire_model()
    from parallel_contracts.domain.fire import FireControls

    text = write_fds(model.content, FireControls(hrr_scale=1.5))
    assert "&HEAD CHID='room_fire'" in text and "HRRPUA=750" in text and text.strip().endswith("&TAIL /")
    ctx = _ctx(tmp_path / "ws", model, FireControls(), "fds")
    res_dir = tmp_path / "res"
    res_dir.mkdir()
    (res_dir / "room_fire_hrr.csv").write_text("s,kW\nTime,HRR\n0.0,0.0\n60.0,80.5\n120.0,79.0\n")
    (res_dir / "room_fire_devc.csv").write_text(
        "s,C,m\nTime,TC_ceiling,VIS_door\n0.0,20.0,30.0\n60.0,310.2,4.5\n"
    )
    (res_dir / "room_fire.out").write_text("...\nSTOP: FDS completed successfully\n")
    res = get_adapter("fds").import_external_result(ctx, res_dir, {})
    by = {(m.metric_id, m.entity): m.value for m in res.metrics}
    assert by[("fire.peak_hrr", None)] == 80.5
    assert by[("fire.device_max_temperature", "TC_ceiling")] == 310.2
    assert by[("fire.device_min_visibility", "VIS_door")] == 4.5
    assert read_fds_csv(res_dir / "room_fire_hrr.csv")[1] == ["Time", "HRR"]


def test_ventsim_licence_and_import(tmp_path):
    from parallel_contracts.domain.ventilation import VentilationControls

    a = get_adapter("ventsim")
    assert a.check_installation().availability == Availability.licence_required
    assert (
        a.check_installation(
            installation={"licence_confirmed": True, "edition": "Ventsim Design (test)"}
        ).availability
        == Availability.ready
    )
    ctx = _ctx(tmp_path / "ws", mine_ventilation_model(), VentilationControls(), "ventsim")
    prep = a.prepare_inputs(ctx)
    pkg = a.build_exchange_package(ctx, prep)
    assert "data/airways.csv" in zipfile.ZipFile(pkg).namelist()
    res_dir = tmp_path / "res"
    res_dir.mkdir()
    (res_dir / "airway_results.csv").write_text(
        "airway_id,airflow_m3s,pressure_drop_pa\nA1,52.0,310\nA2,30.0,90\nA3,22.0,60\n"
    )
    res = a.import_external_result(ctx, res_dir, {})
    assert res.metrics[0].metric_id == "vent.total_airflow" and res.metrics[0].value == 52.0
    (res_dir / "airway_results.csv").write_text("airway_id,airflow_m3s,pressure_drop_pa\nA9,1,1\n")
    with pytest.raises(EngineError):
        a.import_external_result(ctx, res_dir, {})


def test_flac3d_flags_non_specialist_properties_and_imports(tmp_path):
    from parallel_contracts.domain.geomechanics import GeomechControls

    a = get_adapter("flac3d")
    report = a.validate_model(excavation_continuum_model())
    assert report.ok and any(i.code == "specialist_input" for i in report.warnings)
    ctx = _ctx(
        tmp_path / "ws",
        excavation_continuum_model(),
        GeomechControls(active_stages=["stage1", "stage2"]),
        "flac3d",
    )
    prep = a.prepare_inputs(ctx)
    a.build_exchange_package(ctx, prep)
    res_dir = tmp_path / "res"
    res_dir.mkdir()
    (res_dir / "monitoring_results.csv").write_text(
        "point_id,stage_id,ux_m,uy_m,uz_m\ncrest,stage2,0.003,0,-0.004\nbase,stage2,0,0,0.002\n"
    )
    res = a.import_external_result(ctx, res_dir, {})
    assert res.metrics[0].value == pytest.approx(0.005)


def test_virtual_crash_import_computes_evidence_residuals(tmp_path):
    from parallel_contracts.domain.collision import CollisionControls

    a = get_adapter("virtualcrash")
    ctx = _ctx(tmp_path / "ws", collision_case_model(), CollisionControls(hypothesis_id="H1"), "virtualcrash")
    prep = a.prepare_inputs(ctx)
    dxf = (ctx.workspace.input_dir / "road_edges.dxf").read_text()
    assert dxf.startswith("0\nSECTION") and dxf.strip().endswith("EOF")
    a.build_exchange_package(ctx, prep)
    res_dir = tmp_path / "res"
    res_dir.mkdir()
    (res_dir / "trajectories.csv").write_text(
        "veh,time,px,py,yaw,v\nCar A,0,-20,-1.5,0,50\nCar A,1.5,12.2,1.2,21,0\nCar B,0,6,-30,90,40\nCar B,1.5,6.1,-2.3,98,0\n"
    )
    mapping = {
        "vehicle_column": "veh",
        "time_column": "time",
        "x_column": "px",
        "y_column": "py",
        "heading_column": "yaw",
        "speed_column": "v",
        "speed_unit": "km/h",
        "vehicle_id_map": {"Car A": "V1", "Car B": "V2"},
    }
    res = a.import_external_result(ctx, res_dir, {"column_mapping": mapping, "hypothesis_id": "H1"})
    by = {m.metric_id: m.value for m in res.metrics}
    assert by["crash.targets_within_uncertainty"] == 2
    assert by["crash.rms_residual"] == pytest.approx(
        np.sqrt((0.2**2 + 0.2**2 + 0.1**2 + 0.3**2) / 2), rel=1e-6
    )
    assert res.summary["playback_source"] == "imported_reconstruction"


def test_pccrash_import_is_refused_until_documented(tmp_path):
    from parallel_contracts.domain.collision import CollisionControls

    a = get_adapter("pccrash")
    assert a.check_installation().availability == Availability.integration_incomplete
    ctx = _ctx(tmp_path / "ws", collision_case_model(), CollisionControls(hypothesis_id="H1"), "pccrash")
    with pytest.raises(EngineError, match="vendor documentation"):
        a.import_external_result(ctx, tmp_path, {})


def test_isaacsim_unavailable_without_gpu_and_reference_evaluation(tmp_path):
    a = get_adapter("isaacsim")
    assert a.check_installation().availability in (
        Availability.worker_unavailable,
        Availability.configuration_required,
        Availability.integration_incomplete,
        Availability.ready,
    )
    # Evaluation logic of the reference scenario on a synthetic state log (not Isaac Sim output)
    g, h, dt = 9.81, 2.0, 1 / 240
    rows = ["t_s,body,x,y,z,qw,qx,qy,qz,vx,vy,vz"]
    for i in range(0, 400):
        t = i * dt
        z = max(0.25, 0.25 + h - 0.5 * g * t * t)
        rows.append(f"{t},ref_box,0,0,{z},1,0,0,0,0,0,{-g * t if z > 0.25 else 0}")
    p = tmp_path / "states.csv"
    p.write_text("\n".join(rows))
    ev = evaluate_reference(p)
    assert ev["passed"] and abs(ev["error_s"]) < dt * 2


def test_hecras_requires_metadata_and_imports_raster(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    from parallel_contracts.domain import DomainModelEnvelope
    from parallel_contracts.domain.hydraulics2d import (
        BoundaryCondition,
        SurfaceHydraulicControls,
        SurfaceHydraulicModel,
    )
    from parallel_contracts.geometry import LineStringGeom, rect
    from parallel_contracts.provenance import Provenance

    prov = Provenance(kind="assumption")
    model = DomainModelEnvelope(
        discipline="surface_hydraulics",
        name="t",
        content=SurfaceHydraulicModel(
            terrain_asset_id="terrain-1",
            horizontal_crs="EPSG:32735",
            vertical_datum="Local datum (synthetic)",
            perimeter=rect(0, 0, 100, 100),
            cell_size_m=5,
            default_manning_n=0.035,
            boundaries=[
                BoundaryCondition(
                    id="in",
                    name="Inflow",
                    kind="flow_hydrograph",
                    line=LineStringGeom(coordinates=[[0, 40], [0, 60]]),
                    series=[(0, 1), (1, 20)],
                    provenance=prov,
                ),
                BoundaryCondition(
                    id="out",
                    name="Outflow",
                    kind="normal_depth",
                    line=LineStringGeom(coordinates=[[100, 40], [100, 60]]),
                    friction_slope=0.001,
                    provenance=prov,
                ),
            ],
            simulation_start="2026-01-01T00:00:00",
            simulation_end="2026-01-01T06:00:00",
            computation_interval_s=10,
            output_interval_s=600,
        ),
    )
    a = get_adapter("hecras")
    assert a.validate_model(model).ok
    terrain = tmp_path / "terrain.tif"
    with rasterio.open(
        terrain,
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=1,
        dtype="float32",
        crs="EPSG:32735",
        transform=from_origin(600000, 7100100, 10, 10),
    ) as ds:
        ds.write(np.zeros((1, 10, 10), dtype="float32"))
    ctx = RunContext(
        "run-h",
        RunWorkspace(tmp_path / "ws").create(),
        model,
        ScenarioContent(
            discipline="surface_hydraulics", engine_id="hecras", controls=SurfaceHydraulicControls()
        ),
        seed=None,
        asset_path=lambda _id: terrain,
    )
    prep = a.prepare_inputs(ctx)
    pkg = a.build_exchange_package(ctx, prep)
    assert "data/terrain.tif" in zipfile.ZipFile(pkg).namelist()
    res = tmp_path / "res"
    res.mkdir()
    depth = np.zeros((1, 10, 10), dtype="float32")
    depth[0, 2:5, 2:6] = 0.6
    with rasterio.open(
        res / "max_depth.tif",
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=1,
        dtype="float32",
        crs="EPSG:32735",
        transform=from_origin(600000, 7100100, 10, 10),
    ) as ds:
        ds.write(depth)
    (res / "metadata.json").write_text(json.dumps({"hec_ras_version": "6.6", "plan_name": "p"}))
    with pytest.raises(EngineError, match="missing required keys"):
        a.import_external_result(ctx, res, {})
    (res / "metadata.json").write_text(
        json.dumps(
            {
                "hec_ras_version": "6.6",
                "plan_name": "p",
                "terrain": "terrain.tif",
                "mesh": "5 m cells",
                "boundaries": "in, out",
                "time_reference": "2026-01-01T00:00Z",
                "vertical_datum": "Local datum",
            }
        )
    )
    out = a.import_external_result(ctx, res, {})
    by = {m.metric_id: m.value for m in out.metrics}
    assert by["hyd.max_depth"] == pytest.approx(0.6) and by["hyd.inundated_area"] == pytest.approx(12 * 100)
