"""NVIDIA Isaac Sim adapter (GPU worker).

Requires a dedicated runtime: Isaac Sim's standalone Python (``python.sh``) on a
worker with an RTX GPU, configured with PARALLEL_ISAACSIM_PYTHON. Before any
general scene is accepted, the installation must pass the reference box-drop
scenario (analytic free-fall timing check); the verification record is stored
next to the runtime configuration.
"""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
from collections import defaultdict
from pathlib import Path

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.rigid_body import RigidBody, RigidBodyControls, RigidBodyScene
from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck
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
    write_json,
)
from ..capabilities import ISAACSIM
from ..runtime import ProcessLimits, run_process, runner_script, runtimes_dir

REFERENCE_DROP_HEIGHT_M = 2.0  # box bottom height above the ground plane
REFERENCE_TOLERANCE_S = 0.05


def isaac_python() -> str | None:
    p = os.environ.get("PARALLEL_ISAACSIM_PYTHON")
    return p if p and Path(p).exists() else None


def verification_record() -> Path:
    return runtimes_dir() / "isaacsim" / "reference_verification.json"


def reference_scene() -> RigidBodyScene:
    return RigidBodyScene(
        bodies=[
            RigidBody(
                id="ref_box",
                shape="box",
                size=[0.5, 0.5, 0.5],
                mass_kg=10.0,
                position=[0.0, 0.0, REFERENCE_DROP_HEIGHT_M + 0.25],
            )
        ],
        duration_s=1.5,
        physics_dt_s=1 / 240,
    )


class IsaacSimAdapter(EngineAdapter):
    capability = ISAACSIM

    def check_installation(self, mode: ExecutionMode | None = None) -> InstallationCheck:
        mode = mode or ExecutionMode.managed
        if mode == ExecutionMode.connected:
            return self._check(
                mode,
                Availability.integration_incomplete,
                details=["Connected-installation protocol for remote Isaac Sim hosts is not implemented."],
            )
        if shutil.which("nvidia-smi") is None:
            return self._check(
                mode,
                Availability.worker_unavailable,
                details=["No NVIDIA GPU/driver on this worker (nvidia-smi not found)."],
                remedy="Deploy the GPU worker image on an RTX host and route Isaac Sim runs to the 'gpu' queue.",
            )
        py = isaac_python()
        if not py:
            return self._check(
                mode,
                Availability.configuration_required,
                details=["PARALLEL_ISAACSIM_PYTHON is not set to an Isaac Sim python.sh"],
                remedy="Install the selected Isaac Sim release and set PARALLEL_ISAACSIM_PYTHON.",
            )
        rec = verification_record()
        if not rec.exists():
            return self._check(
                mode,
                Availability.integration_incomplete,
                details=["Reference box-drop scenario has not passed on this installation."],
                remedy="Run the reference verification (POST /api/v1/engines/isaacsim/verify).",
            )
        data = json.loads(rec.read_text())
        return self._check(
            mode,
            Availability.ready,
            version=data.get("engine_version"),
            details=[f"Reference verified {data.get('verified_at')}: timing error {data.get('error_s')} s"],
        )

    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport:
        m = model.content
        if not isinstance(m, RigidBodyScene):
            return ValidationReport.from_issues(
                [issue("error", "wrong_model", "Isaac Sim requires a rigid_body scene")]
            )
        issues: list[ValidationIssue] = []
        if m.meters_per_unit != 1.0:
            issues.append(
                issue("error", "scale", "Scenes must be authored in metres (meters_per_unit = 1.0)")
            )
        for b in m.bodies:
            if b.shape == "box" and len(b.size) != 3 or b.shape == "sphere" and len(b.size) != 1:
                issues.append(issue("error", "body_size", f"Body {b.id}: size does not match its shape"))
            if b.provenance.kind.value == "ai_suggested" and not b.provenance.adopted_by:
                issues.append(
                    issue("error", "unadopted", f"Body {b.id} has unadopted AI-suggested properties")
                )
            if b.position[2] < 0:
                issues.append(issue("error", "below_ground", f"Body {b.id} starts below the ground plane"))
        if m.collision_mesh_asset_ids:
            issues.append(
                issue(
                    "info",
                    "collision_meshes",
                    "Collision meshes are separate from the visual reconstruction and carry no material properties.",
                )
            )
        return ValidationReport.from_issues(issues)

    def _validate_controls(
        self, model: DomainModelEnvelope, scenario: ScenarioContent
    ) -> list[ValidationIssue]:
        if not isinstance(scenario.controls, RigidBodyControls):
            return [issue("error", "wrong_controls", "Rigid-body controls required")]
        if not verification_record().exists():
            return [
                issue(
                    "error",
                    "reference_not_verified",
                    "General scene imports are refused until the reference box-drop scenario passes on this installation.",
                )
            ]
        return []

    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m = ctx.model.content
        assert isinstance(m, RigidBodyScene)
        files = {
            "runner_input.json": write_json(
                ctx.workspace.input_dir / "runner_input.json", m.model_dump(mode="json")
            )
        }
        return PreparedInputs(files=files, hashes=hash_inputs(files), summary={"bodies": len(m.bodies)})

    def execute(self, ctx: RunContext, prepared: PreparedInputs, monitor: RunMonitor) -> ExecutionResult:
        py = isaac_python()
        if not py:
            raise EngineError("Isaac Sim runtime not configured", code="configuration_required")
        started = now()
        run_process(
            [
                py,
                str(runner_script("isaacsim_runner.py")),
                str(ctx.workspace.input_dir),
                str(ctx.workspace.output_dir),
            ],
            cwd=ctx.workspace.root,
            log_path=ctx.workspace.log_dir / "isaacsim.log",
            monitor=monitor,
            limits=ProcessLimits(timeout_s=float(ctx.scenario.settings.timeout_s), address_space_mb=None),
            env_extra={"HOME": str(ctx.workspace.root), "ACCEPT_EULA": "Y", "OMNI_KIT_ACCEPT_EULA": "YES"},
            label="isaacsim",
        )
        rr = json.loads((ctx.workspace.output_dir / "runner_result.json").read_text())
        return ExecutionResult(
            engine_version=rr.get("engine_version"),
            runtime=rr,
            exit_code=0,
            stdout_log=ctx.workspace.log_dir / "isaacsim.log",
            started_at=started,
            finished_at=now(),
        )

    def normalise_results(
        self, ctx: RunContext, prepared: PreparedInputs, execution: ExecutionResult
    ) -> NormalisedResult:
        states = read_states(ctx.workspace.output_dir / "states.csv")
        metrics = [
            MetricValue(
                metric_id="rigid.max_speed",
                value=max(math.sqrt(vx * vx + vy * vy + vz * vz) for _, _, _, _, vx, vy, vz in rows),
                unit="m/s",
                entity=bid,
            )
            for bid, rows in states.items()
        ]
        return NormalisedResult(metrics=metrics, units={"position": "m", "velocity": "m/s"})


def read_states(path: Path) -> dict[str, list[tuple[float, float, float, float, float, float, float]]]:
    out: dict[str, list] = defaultdict(list)
    with open(path) as f:
        for r in csv.DictReader(f):
            out[r["body"]].append(tuple(float(r[k]) for k in ("t_s", "x", "y", "z", "vx", "vy", "vz")))
    return dict(out)


def evaluate_reference(states_csv: Path) -> dict:
    """Compare simulated first-contact time with the analytic free-fall time sqrt(2h/g)."""
    rows = read_states(states_csv)["ref_box"]
    g = 9.81
    t_analytic = math.sqrt(2 * REFERENCE_DROP_HEIGHT_M / g)
    contact = next((t for t, _, _, z, _, _, _ in rows if z - 0.25 <= 0.01), None)
    error = None if contact is None else contact - t_analytic
    return {
        "t_analytic_s": t_analytic,
        "t_contact_s": contact,
        "error_s": error,
        "passed": error is not None and abs(error) <= REFERENCE_TOLERANCE_S,
        "tolerance_s": REFERENCE_TOLERANCE_S,
        "tolerance_rationale": "Contact detection at 1/240 s steps plus contact offset; 50 ms bounds both.",
    }
