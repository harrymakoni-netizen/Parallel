"""NIST FDS adapter.

Writes an FDS 6 namelist input from Parallel's fire schema. Managed execution
requires a pinned ``fds`` executable on the worker; otherwise the input is
exchanged with an operator and CHID_hrr.csv / CHID_devc.csv are imported.
Fire and evacuation models remain separate (no FDS+Evac, no implicit coupling).
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.fire import FireControls, FireModel
from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck, ResourceEstimate
from parallel_contracts.metrics import MetricValue
from parallel_contracts.runs import ValidationIssue, ValidationReport
from parallel_contracts.scenario import ScenarioContent

from ..base import (
    EngineAdapter,
    EngineError,
    ExecutionResult,
    NormalisedResult,
    PreparedInputs,
    RunContext,
    RunMonitor,
    hash_inputs,
    issue,
    now,
)
from ..capabilities import FDS
from ..exchange import build_package, readme
from ..runtime import ProcessLimits, run_process

MAX_CELLS = 2_000_000


def fds_executable() -> str | None:
    return os.environ.get("PARALLEL_FDS_BIN") or shutil.which("fds")


def _xb(v: list[float]) -> str:
    return ",".join(f"{x:g}" for x in v)


def write_fds(m: FireModel, c: FireControls) -> str:
    t_end = c.t_end_s or m.t_end_s
    L = [f"&HEAD CHID='{m.chid}', TITLE='{m.title[:60]}' /", f"&TIME T_END={t_end:g} /"]
    for mesh in m.meshes:
        L.append(f"&MESH ID='{mesh.id}', IJK={','.join(map(str, mesh.ijk))}, XB={_xb(mesh.xb)} /")
    L.append(f"&REAC FUEL='{m.fuel}', SOOT_YIELD={m.soot_yield:g}, CO_YIELD={m.co_yield:g} /")
    for s in m.surfaces:
        parts = [f"ID='{s.id}'"]
        if s.hrrpua_kw_m2 is not None:
            parts.append(f"HRRPUA={s.hrrpua_kw_m2 * c.hrr_scale:g}")
        if s.ramp_up_s is not None:
            parts.append(f"TAU_Q=-{s.ramp_up_s:g}")
        if s.color:
            parts.append(f"COLOR='{s.color}'")
        L.append(f"&SURF {', '.join(parts)} /")
    for o in m.obstructions:
        L.append(f"&OBST ID='{o.id}', XB={_xb(o.xb)}, SURF_ID='{o.surf_id}' /")
    for v in m.vents:
        if v.id in c.closed_vents:
            continue
        L.append(f"&VENT ID='{v.id}', XB={_xb(v.xb)}, SURF_ID='{v.surf_id}' /")
    for d in m.devices:
        spec = f", SPEC_ID='{d.spec_id}'" if d.spec_id else ""
        L.append(f"&DEVC ID='{d.id}', QUANTITY='{d.quantity}', XYZ={_xb(d.xyz)}{spec} /")
    L.append("&TAIL /")
    return "\n".join(L) + "\n"


def read_fds_csv(path: Path) -> tuple[list[str], list[str], list[list[float]]]:
    """FDS CSV outputs: line 1 units, line 2 column names, then numeric rows."""
    text = path.read_text(errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 3:
        raise EngineError(f"{path.name}: expected units, header and data rows", deterministic=True)
    units = [u.strip() for u in rows[0]]
    names = [n.strip() for n in rows[1]]
    data = [[float(x) for x in r] for r in rows[2:] if r and any(x.strip() for x in r)]
    if not names or names[0] != "Time":
        raise EngineError(f"{path.name}: first column must be Time", deterministic=True)
    return units, names, data


class FdsAdapter(EngineAdapter):
    capability = FDS

    def default_mode(self) -> ExecutionMode:
        return ExecutionMode.managed if fds_executable() else ExecutionMode.file_exchange

    def check_installation(self, mode: ExecutionMode | None = None) -> InstallationCheck:
        mode = mode or self.default_mode()
        if mode == ExecutionMode.file_exchange:
            return self._check(
                mode, Availability.ready, details=["FDS input generation and CSV import (operator runs FDS)"]
            )
        exe = fds_executable()
        if not exe:
            return self._check(
                mode,
                Availability.installation_required,
                details=["fds executable not found"],
                remedy="Install FDS 6.10.1 and set PARALLEL_FDS_BIN, or use file exchange.",
            )
        try:
            out = subprocess.run([exe], capture_output=True, text=True, timeout=30, input="")
            m = re.search(r"FDS-(\d+\.\d+\.\d+)", out.stdout + out.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._check(mode, Availability.installation_required, details=[str(exc)])
        ver = m.group(1) if m else None
        if ver != self.capability.pinned_version:
            return self._check(mode, Availability.unsupported_version, version=ver)
        return self._check(mode, Availability.ready, version=ver)

    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport:
        m = model.content
        if not isinstance(m, FireModel):
            return ValidationReport.from_issues([issue("error", "wrong_model", "FDS requires a fire model")])
        issues: list[ValidationIssue] = []
        cells = 0
        for mesh in m.meshes:
            i, j, k = mesh.ijk
            cells += i * j * k
            dx = (mesh.xb[1] - mesh.xb[0]) / i
            dy = (mesh.xb[3] - mesh.xb[2]) / j
            dz = (mesh.xb[5] - mesh.xb[4]) / k
            if max(dx, dy, dz) / min(dx, dy, dz) > 2.0:
                issues.append(issue("warning", "cell_aspect", f"Mesh {mesh.id} cell aspect ratio exceeds 2"))
        if cells > MAX_CELLS:
            issues.append(
                issue("error", "mesh_too_large", f"{cells} cells exceeds the {MAX_CELLS} cell limit")
            )
        if not any(s.hrrpua_kw_m2 for s in m.surfaces):
            issues.append(issue("error", "no_fire", "No burner surface (HRRPUA) is defined"))
        for s in m.surfaces:
            if (
                s.hrrpua_kw_m2
                and s.provenance.kind.value in ("ai_suggested",)
                and not s.provenance.adopted_by
            ):
                issues.append(
                    issue(
                        "error",
                        "unadopted_fire_property",
                        f"Fire property of {s.id} is an unadopted AI suggestion",
                    )
                )
        issues.append(
            issue(
                "info",
                "separate_models",
                "Fire results are not coupled to evacuation; any exchange requires a separately specified "
                "and tested coupling.",
            )
        )
        return ValidationReport.from_issues(issues)

    def estimate_resources(self, model: DomainModelEnvelope, scenario: ScenarioContent) -> ResourceEstimate:
        m = model.content
        assert isinstance(m, FireModel)
        cells = sum(a * b * c for a, b, c in (mesh.ijk for mesh in m.meshes))
        return ResourceEstimate(
            cpu_cores=len(m.meshes),
            memory_mb=max(512, cells // 1000),
            expected_runtime_s=None,
            basis=f"{cells} cells; FDS runtime depends strongly on time step and hardware",
        )

    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m, c = ctx.model.content, ctx.scenario.controls
        assert isinstance(m, FireModel) and isinstance(c, FireControls)
        notes = []
        if c.hrr_scale != 1:
            notes.append(f"Burner HRRPUA scaled by {c.hrr_scale:g}.")
        notes += [f"Vent {v} closed (omitted)." for v in c.closed_vents]
        path = ctx.workspace.input_dir / f"{m.chid}.fds"
        path.write_text(write_fds(m, c))
        files = {path.name: path}
        return PreparedInputs(
            files=files, hashes=hash_inputs(files), model_change_notes=notes, summary={"chid": m.chid}
        )

    def execute(self, ctx: RunContext, prepared: PreparedInputs, monitor: RunMonitor) -> ExecutionResult:
        exe = fds_executable()
        if not exe:
            raise EngineError("FDS executable not configured on this worker", code="installation_required")
        started = now()
        m = ctx.model.content
        assert isinstance(m, FireModel)
        out = ctx.workspace.output_dir
        shutil.copy(prepared.files[f"{m.chid}.fds"], out / f"{m.chid}.fds")
        res = run_process(
            [exe, f"{m.chid}.fds"],
            cwd=out,
            log_path=ctx.workspace.log_dir / "fds.log",
            monitor=monitor,
            limits=ProcessLimits(
                timeout_s=float(ctx.scenario.settings.timeout_s), address_space_mb=None, cores=len(m.meshes)
            ),
            label="fds",
        )
        if res.exit_code != 0:
            raise EngineError("FDS exited with an error; see fds.log")
        return ExecutionResult(
            engine_version=self.check_installation(ExecutionMode.managed).detected_version,
            runtime={},
            exit_code=0,
            stdout_log=res.log_path,
            started_at=started,
            finished_at=now(),
        )

    def normalise_results(
        self, ctx: RunContext, prepared: PreparedInputs, execution: ExecutionResult
    ) -> NormalisedResult:
        return self._from_csv(ctx, ctx.workspace.output_dir)

    def build_exchange_package(self, ctx: RunContext, prepared: PreparedInputs) -> Path:
        m = ctx.model.content
        assert isinstance(m, FireModel)
        text = readme(
            "FDS exchange package",
            [
                f"Install FDS {self.capability.pinned_version}.",
                f"Run: fds data/{m.chid}.fds",
                "Check the .out file ends with 'STOP: FDS completed successfully'.",
                f"Zip {m.chid}_hrr.csv, {m.chid}_devc.csv and {m.chid}.out and upload the zip to the waiting run.",
            ],
            {f"{m.chid}.fds": "FDS input generated by Parallel"},
            {
                f"{m.chid}_hrr.csv": "Heat release rate output",
                f"{m.chid}_devc.csv": "Device output",
                f"{m.chid}.out": "FDS run log",
            },
            [
                "Fire results are not coupled to evacuation.",
                "Material properties come from the model with provenance.",
            ],
        )
        return build_package(
            engine_id=self.engine_id,
            engine_name=self.capability.name,
            run_id=ctx.run_id,
            files=dict(prepared.files),
            readme=text,
            result_template={"required": [f"{m.chid}_hrr.csv", f"{m.chid}_devc.csv", f"{m.chid}.out"]},
            dest=ctx.workspace.root / "exchange" / f"{ctx.run_id}-fds-package.zip",
        )

    def import_external_result(
        self, ctx: RunContext, package_dir: Path, options: dict[str, Any]
    ) -> NormalisedResult:
        m = ctx.model.content
        assert isinstance(m, FireModel)
        out = package_dir / f"{m.chid}.out"
        if not out.exists():
            raise EngineError(f"result package must contain {m.chid}.out", deterministic=True)
        if "FDS completed successfully" not in out.read_text(errors="replace"):
            raise EngineError("FDS log does not report successful completion", deterministic=True)
        return self._from_csv(ctx, package_dir)

    def _from_csv(self, ctx: RunContext, d: Path) -> NormalisedResult:
        m = ctx.model.content
        assert isinstance(m, FireModel)
        metrics: list[MetricValue] = []
        _, names, rows = read_fds_csv(d / f"{m.chid}_hrr.csv")
        if "HRR" not in names:
            raise EngineError("HRR column missing from hrr.csv", deterministic=True)
        hi = names.index("HRR")
        metrics.append(MetricValue(metric_id="fire.peak_hrr", value=max(r[hi] for r in rows), unit="kW"))
        units, names, rows = read_fds_csv(d / f"{m.chid}_devc.csv")
        for dev in m.devices:
            if dev.id not in names:
                continue
            k = names.index(dev.id)
            vals = [r[k] for r in rows]
            if dev.quantity == "TEMPERATURE":
                if units[k] not in ("C", "deg C"):
                    raise EngineError(f"device {dev.id}: unexpected unit {units[k]}", deterministic=True)
                metrics.append(
                    MetricValue(
                        metric_id="fire.device_max_temperature", value=max(vals), unit="degC", entity=dev.id
                    )
                )
            elif dev.quantity == "VISIBILITY":
                metrics.append(
                    MetricValue(
                        metric_id="fire.device_min_visibility", value=min(vals), unit="m", entity=dev.id
                    )
                )
        return NormalisedResult(
            metrics=metrics, units={"hrr": "kW", "temperature": "degC", "visibility": "m"}
        )
