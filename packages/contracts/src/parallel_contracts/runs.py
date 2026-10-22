"""Run states, legal transitions and the result envelope (spec section 25)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import SCHEMA_VERSION
from .engines import ExecutionMode
from .metrics import MetricValue


class RunState(StrEnum):
    draft = "draft"
    validating = "validating"
    queued = "queued"
    preparing = "preparing"
    running = "running"
    collecting = "collecting"
    postprocessing = "postprocessing"
    awaiting_external_result = "awaiting_external_result"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_STATES = frozenset({RunState.completed, RunState.failed, RunState.cancelled})
ACTIVE_WORKER_STATES = frozenset(
    {RunState.preparing, RunState.running, RunState.collecting, RunState.postprocessing}
)

LEGAL_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.draft: frozenset({RunState.validating, RunState.cancelled}),
    RunState.validating: frozenset({RunState.queued, RunState.failed, RunState.cancelled}),
    RunState.queued: frozenset({RunState.preparing, RunState.failed, RunState.cancelled}),
    RunState.preparing: frozenset(
        {
            RunState.running,
            RunState.awaiting_external_result,
            RunState.failed,
            RunState.cancelled,
            RunState.queued,
        }
    ),
    RunState.running: frozenset({RunState.collecting, RunState.failed, RunState.cancelled, RunState.queued}),
    RunState.collecting: frozenset(
        {RunState.postprocessing, RunState.failed, RunState.cancelled, RunState.queued}
    ),
    RunState.postprocessing: frozenset(
        {RunState.completed, RunState.failed, RunState.cancelled, RunState.queued}
    ),
    # An operator-supplied result package is validated and normalised (collecting -> postprocessing).
    RunState.awaiting_external_result: frozenset({RunState.collecting, RunState.failed, RunState.cancelled}),
    RunState.completed: frozenset(),
    RunState.failed: frozenset(),
    RunState.cancelled: frozenset(),
}
# Note: transitions back to ``queued`` from worker states are only used by the
# recovery sweep when a worker heartbeat is lost and retries remain.


class IllegalTransition(ValueError):
    pass


def check_transition(current: RunState, new: RunState) -> None:
    if new not in LEGAL_TRANSITIONS[current]:
        raise IllegalTransition(f"Illegal run state transition {current.value} -> {new.value}")


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    path: str | None = None
    hint: str | None = None


class ValidationReport(BaseModel):
    ok: bool
    issues: list[ValidationIssue] = Field(default_factory=list)

    @classmethod
    def from_issues(cls, issues: list[ValidationIssue]) -> ValidationReport:
        return cls(ok=not any(i.severity == "error" for i in issues), issues=issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


class ArtifactRef(BaseModel):
    name: str
    kind: Literal[
        "input", "raw_output", "normalised", "log", "trajectory", "timeseries", "field", "report", "package"
    ]
    media_type: str
    sha256: str
    size_bytes: int
    storage_key: str | None = None
    artifact_id: str | None = None
    description: str | None = None


class ProcessingStep(BaseModel):
    stage: str
    status: Literal["completed", "failed", "skipped", "warning"]
    started_at: datetime
    finished_at: datetime
    message: str | None = None


class EngineMetadata(BaseModel):
    engine_id: str
    engine_version: str | None
    adapter_version: str
    runtime: dict[str, Any] = Field(default_factory=dict)


class RevisionRefs(BaseModel):
    scene_revision_id: str | None = None
    domain_model_revision_id: str
    scenario_revision_id: str


class ImportedProvenance(BaseModel):
    external_installation: str
    supplied_provenance: str
    imported_by: str
    imported_at: datetime
    package_sha256: str


class ResultEnvelope(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION  # type: ignore[assignment]
    run_id: str
    project_id: str
    revisions: RevisionRefs
    engine: EngineMetadata
    execution_mode: ExecutionMode
    seed: int | None = None
    solver_settings: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)
    input_hashes: dict[str, str] = Field(default_factory=dict)
    output_hashes: dict[str, str] = Field(default_factory=dict)
    metrics: list[MetricValue] = Field(default_factory=list)
    trajectories: list[ArtifactRef] = Field(default_factory=list)
    timeseries: list[ArtifactRef] = Field(default_factory=list)
    fields: list[ArtifactRef] = Field(default_factory=list)
    units: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    validation: ValidationReport
    native_crosschecks: list[dict[str, Any]] = Field(
        default_factory=list, description="Normalised vs native-output comparisons with tolerances"
    )
    processing_history: list[ProcessingStep] = Field(default_factory=list)
    imported: ImportedProvenance | None = None
    summary: dict[str, Any] = Field(
        default_factory=dict, description="Small engine-specific summary for display"
    )
