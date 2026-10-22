"""Typed API request and response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from parallel_contracts.coordinates import GeoReference


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PageOut[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------- auth / users / orgs
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class LoginIn(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=320)
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(ORM):
    id: str
    email: str
    display_name: str
    is_platform_admin: bool = False


class MeOut(BaseModel):
    user: UserOut
    organisations: list[dict[str, Any]]


class AuthConfigOut(BaseModel):
    mode: Literal["local", "oidc"]
    oidc_issuer: str | None = None
    oidc_audience: str | None = None


class OrganisationIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9-]{2,80}$")


class OrganisationOut(ORM):
    id: str
    name: str
    slug: str
    settings: dict[str, Any]
    created_at: datetime


class OrgSettingsIn(BaseModel):
    allow_external_ai: bool | None = None
    allow_case_media_to_ai: bool | None = None
    concurrent_run_limit: int | None = Field(default=None, ge=1, le=200)


class MemberIn(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=320)
    display_name: str | None = None
    role: str
    password: str | None = Field(
        default=None, description="Local identity only: initial password for a new user"
    )


class MemberOut(BaseModel):
    user: UserOut
    role: str


# ---------------------------------------------------------------- projects
ProjectType = Literal[
    "mine_evacuation", "crashlens", "construction", "urban_planning", "urban_drainage", "general"
]


class ProjectIn(BaseModel):
    organisation_id: str
    name: str = Field(min_length=2, max_length=200)
    project_type: ProjectType
    description: str = ""
    location_lonlat: list[float] | None = Field(default=None, min_length=2, max_length=2)
    location_label: str | None = None
    georeference: GeoReference | None = None
    template: str | None = Field(default=None, description="Optional demonstration template key")


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    location_lonlat: list[float] | None = None
    location_label: str | None = None
    georeference: GeoReference | None = None


class ProjectOut(BaseModel):
    id: str
    organisation_id: str
    name: str
    project_type: str
    description: str
    location_lonlat: list[float] | None
    location_label: str | None
    georeference: dict | None
    demo_key: str | None
    template: str | None
    created_at: datetime
    my_role: str | None = None


# ---------------------------------------------------------------- assets
class CaptureBatchIn(BaseModel):
    name: str
    kind: Literal["photos", "video", "drone", "gaussian", "point_cloud", "mesh", "terrain", "model_files"]
    notes: str = ""
    captured_at: datetime | None = None


class CaptureBatchOut(ORM):
    id: str
    project_id: str
    name: str
    kind: str
    notes: str
    captured_at: datetime | None
    synthetic: bool
    created_at: datetime


class AssetOut(ORM):
    id: str
    project_id: str
    capture_batch_id: str | None
    kind: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str | None
    status: str
    inspection: dict[str, Any]
    sensitivity: str
    derived_from_id: str | None
    created_at: datetime


class SignedUrlOut(BaseModel):
    url: str
    expires_in_s: int


# ---------------------------------------------------------------- revisions
class RevisionOut(ORM):
    id: str
    number: int
    status: str
    content: dict[str, Any]
    content_sha256: str
    message: str
    created_at: datetime
    published_at: datetime | None


class DomainModelIn(BaseModel):
    name: str
    content: dict[str, Any] = Field(description="DomainModelEnvelope")
    message: str = ""


class DomainModelOut(ORM):
    id: str
    project_id: str
    name: str
    discipline: str
    current_revision_id: str | None
    created_at: datetime


class ModelRevisionOut(RevisionOut):
    validation: dict[str, Any]
    domain_model_id: str


class RevisionIn(BaseModel):
    content: dict[str, Any]
    message: str = ""
    base_revision_id: str | None = Field(
        default=None, description="Revision being edited (optimistic concurrency)"
    )


class ScenarioIn(BaseModel):
    domain_model_id: str
    name: str
    content: dict[str, Any] = Field(description="ScenarioContent")
    message: str = ""


class ScenarioOut(ORM):
    id: str
    project_id: str
    domain_model_id: str
    name: str
    engine_id: str
    is_baseline: bool
    current_revision_id: str | None
    created_at: datetime


class ScenarioRevisionOut(RevisionOut):
    scenario_id: str
    origin: str
    adopted_by: str | None
    adopted_at: datetime | None


# ---------------------------------------------------------------- runs
class RunIn(BaseModel):
    scenario_revision_id: str
    execution_mode: Literal["managed", "connected", "file_exchange"] | None = None
    label: str | None = None


class RunOut(ORM):
    id: str
    project_id: str
    scenario_id: str
    scenario_revision_id: str
    domain_model_revision_id: str
    scene_revision_id: str | None
    engine_id: str
    execution_mode: str
    state: str
    state_history: list[dict[str, Any]]
    label: str | None
    seed: int | None
    settings: dict[str, Any]
    validation: dict[str, Any]
    resource_estimate: dict[str, Any]
    model_change_notes: list[str]
    attempts: int
    progress: float | None
    progress_message: str | None
    cancel_requested: bool
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    error_code: str | None
    error_message: str | None
    engine_version: str | None
    adapter_version: str | None
    environment: dict[str, Any]
    input_hashes: dict[str, str]
    result_summary: dict[str, Any]
    warnings: list[str]
    imported_at: datetime | None
    external_installation: str | None
    created_at: datetime


class MetricOut(ORM):
    metric_id: str
    entity: str | None
    value: float | None
    unit: str
    status: str
    note: str | None
    stats: dict[str, float] | None


class ArtifactOut(ORM):
    id: str
    name: str
    kind: str
    media_type: str
    sha256: str
    size_bytes: int
    description: str | None
    created_at: datetime


class ExternalResultIn(BaseModel):
    external_installation: str = Field(
        min_length=3, description="Which licensed installation produced the result"
    )
    supplied_provenance: str = Field(
        min_length=3, description="Operator's account of how the result was produced"
    )
    options: dict[str, Any] = Field(default_factory=dict, description="Adapter options, e.g. column mapping")


# ---------------------------------------------------------------- comparisons / optimisation / reports / reviews
class ComparisonIn(BaseModel):
    name: str
    baseline_run_id: str
    run_ids: list[str] = Field(min_length=1, max_length=8)


class ComparisonOut(ORM):
    id: str
    project_id: str
    name: str
    baseline_run_id: str
    run_ids: list[str]
    compatibility: dict[str, Any]
    table: list[dict[str, Any]]
    created_at: datetime


class OptimisationIn(BaseModel):
    name: str
    base_scenario_revision_id: str
    objective_metric: str
    objective_entity: str | None = None
    direction: Literal["minimise", "maximise"]
    variables: list[dict[str, Any]] = Field(
        min_length=1, max_length=4, description="[{path, values:[...]}] or [{path, low, high, steps}]"
    )
    constraints: list[dict[str, Any]] = Field(
        default_factory=list, description="[{metric_id, entity?, op: '<='|'>='|'==', value}]"
    )
    budget: int = Field(ge=1, le=60)
    method: Literal["grid", "coordinate"] = "grid"


class OptimisationOut(ORM):
    id: str
    project_id: str
    name: str
    base_scenario_revision_id: str
    definition: dict[str, Any]
    status: str
    candidates: list[dict[str, Any]]
    best_candidate_index: int | None
    stop_reason: str | None
    created_at: datetime
    finished_at: datetime | None


class ReportIn(BaseModel):
    kind: Literal["project", "comparison", "case"]
    title: str
    run_ids: list[str] = Field(default_factory=list)
    comparison_id: str | None = None
    case_id: str | None = None
    narrative: str | None = Field(default=None, max_length=20000)
    narrative_origin: Literal["manual", "ai_draft_adopted"] | None = None
    figure_asset_ids: list[str] = Field(
        default_factory=list, max_length=12, description="Scene screenshots or other non-evidence images to include"
    )


class ReportOut(ORM):
    id: str
    project_id: str
    kind: str
    title: str
    status: str
    run_ids: list[str]
    comparison_id: str | None
    case_id: str | None
    review_status: str
    manifest: dict[str, Any]
    error: str | None
    created_at: datetime
    generated_at: datetime | None


class ReviewIn(BaseModel):
    object_type: Literal[
        "scene_revision",
        "domain_model_revision",
        "scenario_revision",
        "simulation_run",
        "report",
        "evidence_observation",
        "hypothesis",
    ]
    object_id: str
    scope: Literal["geometry", "evidence_annotations", "model_assumptions", "interpretation", "report"]
    status: Literal["approved", "changes_requested", "commented"]
    comment: str = ""


class ReviewOut(ORM):
    id: str
    object_type: str
    object_id: str
    scope: str
    status: str
    comment: str
    reviewer_id: str | None
    created_at: datetime
