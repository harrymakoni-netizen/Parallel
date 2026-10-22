"""EnergyPlus adapter with OpenStudio model preparation.

* Preparation (OpenStudio SDK in its own runtime): thermal schema -> .osm -> .idf.
* Managed execution: requires a pinned EnergyPlus executable on the worker.
* File exchange: the IDF package is run by an operator; ``eplusout.sql`` is
  imported and parsed with the documented EnergyPlus SQLite output schema.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.thermal import ThermalControls, ThermalModel
from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck
from parallel_contracts.metrics import MetricStatus, MetricValue
from parallel_contracts.runs import ValidationIssue, ValidationReport
from parallel_contracts.scenario import ScenarioContent
from parallel_scene.geometry import validate_polygon

from ..base import (
    EngineAdapter,
    EngineError,
    ExecutionResult,
    NormalisedResult,
    NullMonitor,
    PreparedInputs,
    RunContext,
    RunMonitor,
    hash_inputs,
    issue,
    now,
    write_json,
)
from ..capabilities import ENERGYPLUS
from ..exchange import build_package, readme
from ..runtime import ProcessLimits, pinned_requirements, probe_python_runtime, run_python_runner

J_PER_KWH = 3.6e6


def energyplus_executable() -> str | None:
    return os.environ.get("PARALLEL_ENERGYPLUS_BIN") or shutil.which("energyplus")


def energyplus_version(exe: str) -> str | None:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"Version (\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


class EnergyPlusAdapter(EngineAdapter):
    capability = ENERGYPLUS

    def default_mode(self) -> ExecutionMode:
        return ExecutionMode.managed if energyplus_executable() else ExecutionMode.file_exchange

    def check_installation(self, mode: ExecutionMode | None = None) -> InstallationCheck:
        mode = mode or self.default_mode()
        ok, out = probe_python_runtime(
            "openstudio",
            "import openstudio; print(openstudio.openStudioVersion(), openstudio.energyPlusVersion())",
        )
        if not ok:
            return self._check(
                mode,
                Availability.installation_required,
                details=[f"OpenStudio runtime: {out}"],
                remedy="Run scripts/setup_runtimes.sh openstudio on the simulation worker.",
            )
        os_ver, target = out.split()
        if os_ver != pinned_requirements("openstudio").get("openstudio"):
            return self._check(mode, Availability.unsupported_version, version=f"OpenStudio {os_ver}")
        details = [f"OpenStudio {os_ver} (targets EnergyPlus {target}) ready for model preparation"]
        if mode == ExecutionMode.file_exchange:
            return self._check(
                mode,
                Availability.ready,
                version=f"OpenStudio {os_ver} -> IDF for EnergyPlus {target}",
                details=details + ["EnergyPlus is run by an operator (file exchange)."],
            )
        exe = energyplus_executable()
        if not exe:
            return self._check(
                mode,
                Availability.installation_required,
                version=f"OpenStudio {os_ver}",
                details=details + ["EnergyPlus executable not found"],
                remedy=f"Install EnergyPlus {target} and set PARALLEL_ENERGYPLUS_BIN, or use file exchange.",
            )
        ev = energyplus_version(exe)
        if ev != self.capability.pinned_version:
            return self._check(
                mode,
                Availability.unsupported_version,
                version=ev,
                details=details + [f"EnergyPlus {ev} found, {self.capability.pinned_version} pinned"],
            )
        return self._check(mode, Availability.ready, version=f"EnergyPlus {ev}", details=details)

    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport:
        m = model.content
        if not isinstance(m, ThermalModel):
            return ValidationReport.from_issues(
                [issue("error", "wrong_model", "EnergyPlus requires a thermal model")]
            )
        issues: list[ValidationIssue] = []
        for i, z in enumerate(m.zones):
            issues += validate_polygon(z.footprint, f"content.zones[{i}].footprint", f"Zone {z.id} footprint")
        if not m.design_days and not m.weather_asset_id:
            issues.append(issue("error", "no_weather", "Provide design days or a weather file (EPW) asset"))
        for label, prov in (
            ("Envelope U-values", m.envelope.provenance),
            ("Internal loads", m.loads.provenance),
        ):
            if prov.kind.value in ("assumption", "synthetic"):
                issues.append(
                    issue(
                        "info",
                        "assumed_inputs",
                        f"{label} are explicit assumptions (not inferred from the building's appearance).",
                    )
                )
        return ValidationReport.from_issues(issues)

    def _validate_controls(
        self, model: DomainModelEnvelope, scenario: ScenarioContent
    ) -> list[ValidationIssue]:
        m, c = model.content, scenario.controls
        if not isinstance(m, ThermalModel) or not isinstance(c, ThermalControls):
            return [issue("error", "wrong_controls", "Thermal scenario controls required")]
        if c.run == "annual" and not m.weather_asset_id:
            return [issue("error", "weather_required", "Annual runs need a weather file asset")]
        if c.run == "design_days" and not m.design_days:
            return [issue("error", "design_days_required", "Design-day runs need design days")]
        return []

    def _spec(self, ctx: RunContext) -> tuple[dict[str, Any], list[str]]:
        m, c = ctx.model.content, ctx.scenario.controls
        assert isinstance(m, ThermalModel) and isinstance(c, ThermalControls)
        env = m.envelope.model_dump()
        loads = m.loads.model_dump()
        notes = []
        for key, src, dst in (
            ("wall_u_w_m2k", c.wall_u_w_m2k, env),
            ("window_u_w_m2k", c.window_u_w_m2k, env),
            ("window_to_wall_ratio", c.window_to_wall_ratio, env),
            ("heating_setpoint_c", c.heating_setpoint_c, loads),
            ("cooling_setpoint_c", c.cooling_setpoint_c, loads),
            ("lighting_w_m2", c.lighting_w_m2, loads),
        ):
            if src is not None:
                notes.append(f"{key} changed from {dst[key]} to {src}.")
                dst[key] = src
        spec = {
            **m.model_dump(
                mode="json",
                include={
                    "north_axis_deg",
                    "latitude_deg",
                    "longitude_deg",
                    "time_zone_h",
                    "elevation_m",
                    "zones",
                    "design_days",
                },
            ),
            "envelope": env,
            "loads": loads,
            "run": c.run,
        }
        if c.run == "annual" and m.weather_asset_id and ctx.asset_path:
            spec["weather_path"] = str(ctx.asset_path(m.weather_asset_id))
        return spec, notes

    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        spec, notes = self._spec(ctx)
        files = {"thermal_input.json": write_json(ctx.workspace.input_dir / "thermal_input.json", spec)}
        return PreparedInputs(
            files=files,
            hashes=hash_inputs(files),
            model_change_notes=notes,
            summary={"run": spec["run"], "zones": len(spec["zones"])},
        )

    def _translate(self, ctx: RunContext, monitor: RunMonitor, run_eplus: bool) -> dict:
        args = [str(ctx.workspace.input_dir), str(ctx.workspace.output_dir)]
        if run_eplus:
            exe = energyplus_executable()
            if not exe:
                raise EngineError("EnergyPlus executable not configured", code="installation_required")
            args.append(exe)
        run_python_runner(
            "openstudio",
            "openstudio_runner.py",
            cwd=ctx.workspace.root,
            args=args,
            log_path=ctx.workspace.log_dir / "openstudio.log",
            monitor=monitor,
            limits=ProcessLimits(timeout_s=float(ctx.scenario.settings.timeout_s), address_space_mb=None),
        )
        return json.loads((ctx.workspace.output_dir / "runner_result.json").read_text())

    def execute(self, ctx: RunContext, prepared: PreparedInputs, monitor: RunMonitor) -> ExecutionResult:
        started = now()
        rr = self._translate(ctx, monitor, run_eplus=True)
        exe = energyplus_executable()
        return ExecutionResult(
            engine_version=energyplus_version(exe) if exe else None,
            runtime=rr,
            exit_code=0,
            stdout_log=ctx.workspace.output_dir / "energyplus.log",
            started_at=started,
            finished_at=now(),
        )

    def normalise_results(
        self, ctx: RunContext, prepared: PreparedInputs, execution: ExecutionResult
    ) -> NormalisedResult:
        sql = ctx.workspace.output_dir / "eplus" / "eplusout.sql"
        return self._from_sql(sql, prepared.summary)

    def build_exchange_package(self, ctx: RunContext, prepared: PreparedInputs) -> Path:
        rr = self._translate(ctx, NullMonitor(), run_eplus=False)
        o = ctx.workspace.output_dir
        files = {
            "in.idf": o / "in.idf",
            "in.osm": o / "in.osm",
            "thermal_input.json": prepared.files["thermal_input.json"],
        }
        spec = json.loads(prepared.files["thermal_input.json"].read_text())
        if spec.get("weather_path"):
            files["weather.epw"] = Path(spec["weather_path"])
        text = readme(
            f"EnergyPlus {rr['target_energyplus_version']} exchange package",
            [
                f"Install EnergyPlus {rr['target_energyplus_version']} (the version the OpenStudio translation targets).",
                "Run (with -x to expand the HVACTemplate ideal loads objects): energyplus -x -d out -p eplus -s C "
                + ("-w data/weather.epw " if "weather.epw" in files else "")
                + "data/in.idf",
                "Confirm out/eplusout.err has no Severe or Fatal errors.",
                "Zip out/eplusout.sql and out/eplusout.err and upload the zip to the waiting run in Parallel.",
            ],
            {
                "in.idf": "EnergyPlus input generated by OpenStudio",
                "in.osm": "OpenStudio model",
                "thermal_input.json": "Parallel thermal inputs with provenance",
            },
            {
                "eplusout.sql": "EnergyPlus SQLite output (required)",
                "eplusout.err": "EnergyPlus error file (required)",
            },
            ["Ideal loads air systems only.", "Schedules are constant (explicit assumption)."],
        )
        return build_package(
            engine_id=self.engine_id,
            engine_name=self.capability.name,
            run_id=ctx.run_id,
            files=files,
            readme=text,
            result_template={"required": ["eplusout.sql", "eplusout.err"]},
            dest=ctx.workspace.root / "exchange" / f"{ctx.run_id}-energyplus-package.zip",
            extra={"openstudio": rr},
        )

    def import_external_result(
        self, ctx: RunContext, package_dir: Path, options: dict[str, Any]
    ) -> NormalisedResult:
        sql, err = package_dir / "eplusout.sql", package_dir / "eplusout.err"
        if not sql.exists() or not err.exists():
            raise EngineError("result package must contain eplusout.sql and eplusout.err", deterministic=True)
        text = err.read_text(errors="replace")
        if "** Fatal" in text or "EnergyPlus Terminated--Fatal" in text:
            raise EngineError(
                "eplusout.err reports a fatal error; the run did not complete", deterministic=True
            )
        res = self._from_sql(sql, {})
        sev = len(re.findall(r"\*\* Severe", text))
        if sev:
            res.warnings.append(f"eplusout.err reports {sev} severe error(s).")
        return res

    def _from_sql(self, sql: Path, summary: dict) -> NormalisedResult:
        if not sql.exists():
            raise EngineError("EnergyPlus SQLite output not found", deterministic=True)
        con = sqlite3.connect(f"file:{sql}?mode=ro", uri=True)
        try:

            def series(name: str) -> dict[int, float]:
                rows = con.execute(
                    "SELECT rd.TimeIndex, SUM(rd.Value) FROM ReportData rd "
                    "JOIN ReportDataDictionary d ON rd.ReportDataDictionaryIndex = d.ReportDataDictionaryIndex "
                    "JOIN Time t ON rd.TimeIndex = t.TimeIndex "
                    "WHERE d.Name = ? AND (t.WarmupFlag IS NULL OR t.WarmupFlag = 0) GROUP BY rd.TimeIndex",
                    (name,),
                ).fetchall()
                return {int(a): float(b) for a, b in rows}

            heat = series("Zone Ideal Loads Supply Air Total Heating Rate")
            cool = series("Zone Ideal Loads Supply Air Total Cooling Rate")
            heat_e = series("Zone Ideal Loads Supply Air Total Heating Energy")
            cool_e = series("Zone Ideal Loads Supply Air Total Cooling Energy")
            version = con.execute("SELECT EnergyPlusVersion FROM Simulations LIMIT 1").fetchone()
        finally:
            con.close()

        def mv(mid: str, vals: dict[int, float], unit: str, agg) -> MetricValue:
            if not vals:
                return MetricValue(
                    metric_id=mid,
                    value=None,
                    unit=unit,
                    status=MetricStatus.unavailable,
                    note="Variable not found in eplusout.sql",
                )
            return MetricValue(metric_id=mid, value=agg(vals.values()), unit=unit)

        metrics = [
            mv("thermal.peak_heating_load", heat, "W", max),
            mv("thermal.peak_cooling_load", cool, "W", max),
            mv("thermal.heating_energy", heat_e, "kWh", lambda v: sum(v) / J_PER_KWH),
            mv("thermal.cooling_energy", cool_e, "kWh", lambda v: sum(v) / J_PER_KWH),
        ]
        return NormalisedResult(
            metrics=metrics,
            summary={**summary, "energyplus_version": version[0] if version else None},
            units={"power": "W", "energy": "kWh"},
        )
