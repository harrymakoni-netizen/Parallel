"""Typed engine adapter contract (spec section 15.1).

Responsibilities map to the specification's contract list:

* describe_capabilities  -> ``EngineAdapter.capability``
* check_installation     -> ``check_installation()``
* validate_model         -> ``validate_model()``
* validate_scenario      -> ``validate_scenario()``
* estimate_resources     -> ``estimate_resources()``
* prepare_inputs         -> ``prepare_inputs()``
* submit / get_status / cancel -> ``execute()`` driving a ``RuntimeProcess`` with a ``RunMonitor``
  (heartbeats, progress, cancellation and timeouts) for managed execution, or
  ``build_exchange_package()`` / ``import_external_result()`` for file exchange
* collect_raw_outputs    -> ``collect_raw_outputs()``
* normalise_results      -> ``normalise_results()``
"""

from __future__ import annotations

import abc
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.engines import (
    Availability,
    EngineCapability,
    ExecutionMode,
    InstallationCheck,
    ResourceEstimate,
)
from parallel_contracts.metrics import MetricValue
from parallel_contracts.runs import ValidationIssue, ValidationReport
from parallel_contracts.scenario import ScenarioContent
from parallel_metrics.registry import METRICS


class EngineError(RuntimeError):
    """Execution failure. ``deterministic`` failures (invalid model) must not be retried."""

    def __init__(self, message: str, *, deterministic: bool = False, code: str = "engine_error"):
        super().__init__(message)
        self.deterministic = deterministic
        self.code = code


class RunCancelled(RuntimeError):
    pass


class RunTimedOut(EngineError):
    def __init__(self, message: str):
        super().__init__(message, deterministic=False, code="timeout")


class RunMonitor(Protocol):
    """Implemented by the worker: persists heartbeats/progress and reports cancellation."""

    def heartbeat(self, progress: float | None = None, message: str | None = None) -> None: ...

    def cancel_requested(self) -> bool: ...

    def log(self, line: str) -> None: ...


class NullMonitor:
    def __init__(self) -> None:
        self.messages: list[tuple[float | None, str | None]] = []
        self.lines: list[str] = []

    def heartbeat(self, progress: float | None = None, message: str | None = None) -> None:
        self.messages.append((progress, message))

    def cancel_requested(self) -> bool:
        return False

    def log(self, line: str) -> None:
        self.lines.append(line)


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def sha256_json(data: Any) -> str:
    return hashlib.sha256(canonical_json(data)).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class RunWorkspace:
    root: Path

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def normalised_dir(self) -> Path:
        return self.root / "normalised"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    def create(self) -> RunWorkspace:
        for d in (self.input_dir, self.output_dir, self.normalised_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


@dataclass
class RunContext:
    run_id: str
    workspace: RunWorkspace
    model: DomainModelEnvelope
    scenario: ScenarioContent
    seed: int | None
    asset_path: Callable[[str], Path] | None = None  # resolves an asset id to a local file
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreparedInputs:
    files: dict[str, Path]  # logical name -> path (all files inside workspace.input_dir)
    hashes: dict[str, str]
    model_change_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    engine_version: str | None
    runtime: dict[str, Any]
    exit_code: int
    stdout_log: Path | None
    warnings: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=now)
    finished_at: datetime = field(default_factory=now)


@dataclass
class NormalisedFile:
    name: str
    path: Path
    kind: str  # trajectory | timeseries | field | normalised
    media_type: str
    description: str


@dataclass
class NormalisedResult:
    metrics: list[MetricValue]
    files: list[NormalisedFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    crosschecks: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)

    def check_registered(self) -> None:
        unknown = sorted({m.metric_id for m in self.metrics if m.metric_id not in METRICS})
        if unknown:
            raise EngineError(f"normaliser emitted undocumented metrics: {unknown}", deterministic=True)
        for m in self.metrics:
            if m.unit != METRICS[m.metric_id].unit:
                raise EngineError(
                    f"metric {m.metric_id} emitted in {m.unit}, registry defines {METRICS[m.metric_id].unit}",
                    deterministic=True,
                )


def issue(
    severity: str, code: str, message: str, path: str | None = None, hint: str | None = None
) -> ValidationIssue:
    return ValidationIssue(severity=severity, code=code, message=message, path=path, hint=hint)  # type: ignore[arg-type]


def crosscheck(
    name: str, normalised: float | None, native: float | None, abs_tol: float, rel_tol: float, why: str
) -> dict[str, Any]:
    """Record a comparison between a normalised value and the engine's own native output."""
    if normalised is None or native is None:
        passed = normalised is None and native is None
    else:
        passed = abs(normalised - native) <= max(abs_tol, rel_tol * abs(native))
    return {
        "name": name,
        "normalised": normalised,
        "native": native,
        "abs_tolerance": abs_tol,
        "rel_tolerance": rel_tol,
        "passed": bool(passed),
        "tolerance_rationale": why,
    }


class EngineAdapter(abc.ABC):
    capability: EngineCapability
    adapter_version: str = "0.1.0"

    @property
    def engine_id(self) -> str:
        return self.capability.engine_id

    # -- availability -------------------------------------------------------------
    @abc.abstractmethod
    def check_installation(self, mode: ExecutionMode | None = None) -> InstallationCheck: ...

    def default_mode(self) -> ExecutionMode:
        return self.capability.execution_modes[0]

    # -- validation ---------------------------------------------------------------
    @abc.abstractmethod
    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport: ...

    def validate_scenario(self, model: DomainModelEnvelope, scenario: ScenarioContent) -> ValidationReport:
        issues = list(self.validate_model(model).issues)
        if scenario.engine_id != self.engine_id:
            issues.append(
                issue(
                    "error", "engine_mismatch", f"Scenario targets {scenario.engine_id}, not {self.engine_id}"
                )
            )
        if model.discipline != scenario.discipline:
            issues.append(
                issue(
                    "error",
                    "discipline_mismatch",
                    f"Scenario discipline {scenario.discipline} does not match model {model.discipline}",
                )
            )
        if model.discipline.value not in self.capability.disciplines:
            issues.append(
                issue(
                    "error",
                    "unsupported_discipline",
                    f"{self.capability.name} does not support discipline {model.discipline}",
                )
            )
        issues.extend(self._validate_controls(model, scenario))
        return ValidationReport.from_issues(issues)

    def _validate_controls(
        self, model: DomainModelEnvelope, scenario: ScenarioContent
    ) -> list[ValidationIssue]:
        return []

    def estimate_resources(self, model: DomainModelEnvelope, scenario: ScenarioContent) -> ResourceEstimate:
        return ResourceEstimate(basis="No engine-specific estimate; default single-core allocation.")

    # -- managed execution --------------------------------------------------------
    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        raise EngineError(
            f"{self.capability.name}: managed input preparation is not implemented", deterministic=True
        )

    def execute(self, ctx: RunContext, prepared: PreparedInputs, monitor: RunMonitor) -> ExecutionResult:
        raise EngineError(f"{self.capability.name}: managed execution is not available", deterministic=True)

    def collect_raw_outputs(self, ctx: RunContext) -> list[Path]:
        return sorted(p for p in ctx.workspace.output_dir.rglob("*") if p.is_file())

    def normalise_results(
        self, ctx: RunContext, prepared: PreparedInputs, execution: ExecutionResult
    ) -> NormalisedResult:
        raise EngineError(
            f"{self.capability.name}: result normalisation is not implemented", deterministic=True
        )

    # -- file exchange ------------------------------------------------------------
    def build_exchange_package(self, ctx: RunContext, prepared: PreparedInputs) -> Path:
        raise EngineError(f"{self.capability.name}: no file-exchange package is defined", deterministic=True)

    def import_external_result(
        self, ctx: RunContext, package_dir: Path, options: dict[str, Any]
    ) -> NormalisedResult:
        raise EngineError(f"{self.capability.name}: no external result import is defined", deterministic=True)

    # -- helpers --------------------------------------------------------------------
    def _check(
        self,
        mode: ExecutionMode,
        availability: Availability,
        *,
        version: str | None = None,
        details: list[str] | None = None,
        remedy: str | None = None,
        worker: str | None = None,
    ) -> InstallationCheck:
        return InstallationCheck(
            engine_id=self.engine_id,
            execution_mode=mode,
            availability=availability,
            detected_version=version,
            expected_version=self.capability.pinned_version,
            checked_at=now(),
            worker=worker,
            details=details or [],
            remedy=remedy,
        )


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str))
    return path


def hash_inputs(files: dict[str, Path]) -> dict[str, str]:
    return {name: sha256_file(p) for name, p in sorted(files.items())}
