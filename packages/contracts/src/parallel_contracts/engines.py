"""Engine capability records, execution modes and availability (spec section 15)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ExecutionMode(StrEnum):
    managed = "managed"  # Parallel prepares and executes the engine on a configured worker
    connected = "connected"  # Parallel talks to an authorised external installation
    file_exchange = "file_exchange"  # verified input/output packages via a documented operator workflow


EXECUTION_MODE_LABELS = {
    ExecutionMode.managed: "Managed execution",
    ExecutionMode.connected: "Connected installation",
    ExecutionMode.file_exchange: "File exchange (operator workflow, not automated execution)",
}


class Availability(StrEnum):
    ready = "ready"
    installation_required = "installation_required"
    configuration_required = "configuration_required"
    licence_required = "licence_required"
    worker_unavailable = "worker_unavailable"
    unsupported_version = "unsupported_version"
    integration_incomplete = "integration_incomplete"


AVAILABILITY_LABELS = {
    Availability.ready: "Ready",
    Availability.installation_required: "Installation required",
    Availability.configuration_required: "Configuration required",
    Availability.licence_required: "Licence required",
    Availability.worker_unavailable: "Worker unavailable",
    Availability.unsupported_version: "Unsupported version",
    Availability.integration_incomplete: "Integration incomplete",
}


class LicenceInfo(BaseModel):
    kind: Literal["open_source", "public_domain", "commercial", "free_proprietary"]
    name: str
    requires_key: bool = False
    note: str | None = None


class TestedVersion(BaseModel):
    version: str
    platform: str
    verified_on: str | None = Field(default=None, description="Date of the recorded verification")
    evidence: str | None = Field(default=None, description="Test id or execution record documenting the verification")
    status: Literal["verified", "awaiting_environment", "not_verified"] = "not_verified"


class EngineCapability(BaseModel):
    engine_id: str
    name: str
    vendor: str
    role: str
    disciplines: list[str]
    dimensions: list[str]
    model_formats_in: list[str]
    model_formats_out: list[str]
    input_units: dict[str, str]
    output_units: dict[str, str]
    runtime_requirements: list[str]
    operating_systems: list[str]
    licence: LicenceInfo
    execution_modes: list[ExecutionMode]
    cancellation: str
    resource_estimate: str
    result_types: list[str]
    limitations: list[str]
    tested_versions: list[TestedVersion]
    pinned_version: str | None = None
    official_docs: list[str] = Field(default_factory=list)
    adapter_version: str
    companion_tools: list[str] = Field(default_factory=list, description="e.g. OpenStudio for EnergyPlus")


class InstallationCheck(BaseModel):
    engine_id: str
    execution_mode: ExecutionMode
    availability: Availability
    detected_version: str | None = None
    expected_version: str | None = None
    checked_at: datetime
    worker: str | None = None
    details: list[str] = Field(default_factory=list)
    remedy: str | None = Field(default=None, description="Actionable next step to make the engine ready")


class ResourceEstimate(BaseModel):
    cpu_cores: int = 1
    memory_mb: int = 512
    gpu: bool = False
    expected_runtime_s: float | None = None
    basis: str = Field(description="How the estimate was derived")
    within_limits: bool = True
    limit_notes: list[str] = Field(default_factory=list)
