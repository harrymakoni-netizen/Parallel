"""EnergyPlus/OpenStudio: model preparation verified; EnergyPlus execution awaits an installation."""

import json
import sqlite3
import zipfile

import pytest

from parallel_contracts.engines import Availability, ExecutionMode
from parallel_engines.base import RunContext, RunWorkspace
from parallel_engines.reference_models import office_thermal_model, thermal_scenario
from parallel_engines.registry import get_adapter

from .conftest import require_engine


def test_openstudio_translation_and_exchange_package(workdir):
    adapter = get_adapter("energyplus")
    check = adapter.check_installation(ExecutionMode.file_exchange)
    if check.availability != Availability.ready:
        require_engine("energyplus")
    ws = RunWorkspace(workdir).create()
    ctx = RunContext("r1", ws, office_thermal_model(), thermal_scenario(wall_u_w_m2k=0.2), seed=None)
    prepared = adapter.prepare_inputs(ctx)
    assert any("wall_u_w_m2k changed" in n for n in prepared.model_change_notes)
    pkg = adapter.build_exchange_package(ctx, prepared)
    names = zipfile.ZipFile(pkg).namelist()
    assert {"README.md", "manifest.json", "data/in.idf", "data/in.osm"} <= set(names)
    facts = json.loads((ws.output_dir / "runner_result.json").read_text())["model"]
    assert facts["floor_area_m2"] == pytest.approx(900.0)
    assert facts["windows"] == 8
    idf = (ws.output_dir / "in.idf").read_text()
    assert "SizingPeriod:DesignDay" in idf and "Output:SQLite" in idf
    # OpenStudio 3.11 emits HVACTemplate ideal loads: EnergyPlus must run with -x (ExpandObjects)
    assert "HVACTemplate:Zone:IdealLoadsAirSystem" in idf
    assert "energyplus -x" in zipfile.ZipFile(pkg).read("README.md").decode()
    manifest = json.loads(zipfile.ZipFile(pkg).read("manifest.json"))
    assert manifest["execution_mode"] == "file_exchange" and "not automated execution" in manifest["label"]


def test_managed_mode_reports_missing_energyplus_honestly(monkeypatch):
    monkeypatch.delenv("PARALLEL_ENERGYPLUS_BIN", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    check = get_adapter("energyplus").check_installation(ExecutionMode.managed)
    assert check.availability in (Availability.installation_required, Availability.ready)
    if check.availability == Availability.installation_required:
        assert "EnergyPlus" in (check.remedy or "")


def test_sql_import_parser_against_documented_schema(workdir):
    """Parser unit test with a minimal database built to the documented EnergyPlus SQLite schema.

    This verifies the query logic only; it is NOT evidence of compatibility with a real EnergyPlus output.
    """
    db = workdir / "eplusout.sql"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE Simulations (SimulationIndex INTEGER, EnergyPlusVersion TEXT);
        CREATE TABLE Time (TimeIndex INTEGER, WarmupFlag INTEGER);
        CREATE TABLE ReportDataDictionary (ReportDataDictionaryIndex INTEGER, Name TEXT, KeyValue TEXT, Units TEXT);
        CREATE TABLE ReportData (ReportDataIndex INTEGER, TimeIndex INTEGER, ReportDataDictionaryIndex INTEGER, Value REAL);
        INSERT INTO Simulations VALUES (1, 'EnergyPlus, Version 25.2.0');
        INSERT INTO Time VALUES (1, 1), (2, 0), (3, 0);
        INSERT INTO ReportDataDictionary VALUES (1, 'Zone Ideal Loads Supply Air Total Heating Rate', 'Z1', 'W'),
                                               (2, 'Zone Ideal Loads Supply Air Total Heating Energy', 'Z1', 'J');
        INSERT INTO ReportData VALUES (1, 1, 1, 99999.0), (2, 2, 1, 5000.0), (3, 3, 1, 7000.0),
                                      (4, 2, 2, 3.6e6), (5, 3, 2, 7.2e6);
    """)
    con.commit()
    con.close()
    (workdir / "eplusout.err").write_text("   ************* EnergyPlus Completed Successfully.\n")
    ws = RunWorkspace(workdir / "ws").create()
    ctx = RunContext("r1", ws, office_thermal_model(), thermal_scenario(), seed=None)
    res = get_adapter("energyplus").import_external_result(ctx, workdir, {})
    by = {m.metric_id: m for m in res.metrics}
    assert by["thermal.peak_heating_load"].value == 7000.0  # warm-up row excluded
    assert by["thermal.heating_energy"].value == pytest.approx(3.0)
    assert by["thermal.peak_cooling_load"].value is None
