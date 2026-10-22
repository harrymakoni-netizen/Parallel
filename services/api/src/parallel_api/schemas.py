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
    case_id: str | None = Field(default=None, description="CrashLens: scope the model to one case")


class DomainModelOut(ORM):
    id: str
    project_id: str
    case_id: str | None = None
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
    case_id: str | None = None
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
    case_id: str | None = None
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


# ---------------------------------------------------------------- CrashLens
class CaseIn(BaseModel):
    reference: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=2, max_length=300)
    incident_at: datetime | None = None
    location_note: str = ""
    training: bool = False


class CasePatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=300)
    status: Literal["open", "closed"] | None = None
    location_note: str | None = None


class CaseOut(ORM):
    id: str
    project_id: str
    reference: str
    title: str
    status: str
    incident_at: datetime | None
    location_note: str
    training: bool
    created_at: datetime
    my_role: str | None = None


class AssignmentIn(BaseModel):
    user_id: str
    role: Literal["investigator", "reviewer"]


class AssignmentOut(ORM):
    id: str
    case_id: str
    user_id: str
    role: str
    assigned_by: str | None
    created_at: datetime


class EvidenceOut(ORM):
    id: str
    case_id: str
    source_asset_id: str | None
    kind: str
    title: str
    description: str
    claimed_captured_at: datetime | None
    captured_by: str | None
    source_metadata: dict[str, Any]
    original_sha256: str | None
    received_by: str | None
    created_at: datetime
    integrity_note: str = (
        "A hash shows that a file is identical to the file that was hashed at receipt. It does not establish "
        "that the contents are accurate or that embedded timestamps are authentic."
    )


class HandlingEventOut(ORM):
    id: str
    evidence_item_id: str
    action: str
    actor_id: str | None
    details: dict[str, Any]
    created_at: datetime


class DerivativeOut(ORM):
    id: str
    evidence_item_id: str
    kind: str
    source_sha256: str
    derived_asset_id: str
    method: str
    created_by: str | None
    created_at: datetime


SPATIAL_OBSERVATION_KINDS = {"skid_mark", "yaw_mark", "debris", "final_position", "scene_measurement"}


class ObservationIn(BaseModel):
    statement_type: Literal["observation", "interpretation"]
    kind: Literal[
        "skid_mark",
        "yaw_mark",
        "debris",
        "final_position",
        "road_condition",
        "visibility",
        "vehicle_damage",
        "scene_measurement",
        "other",
    ]
    text: str = Field(min_length=3, max_length=5000)
    evidence_item_id: str | None = None
    based_on: list[str] = Field(default_factory=list, description="Interpretations: observation ids reasoned from")
    position: list[float] | None = Field(default=None, min_length=2, max_length=3)
    geometry: dict[str, Any] | None = Field(default=None, description="Point/LineString/Polygon in local metres")
    measurement_value: float | None = None
    measurement_unit: str | None = None
    uncertainty: dict[str, Any] | None = Field(default=None, description="Uncertainty contract")
    limitation: str = ""


class ObservationOut(ORM):
    id: str
    case_id: str
    statement_type: str
    kind: str
    text: str
    evidence_item_id: str | None
    based_on: list[str]
    position: list[float] | None
    geometry: dict[str, Any] | None
    measurement_value: float | None
    measurement_unit: str | None
    uncertainty: dict[str, Any] | None
    limitation: str
    review_status: str
    author_id: str | None
    created_at: datetime


class VehicleIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    make: str | None = None
    model: str | None = None
    year: int | None = Field(default=None, ge=1886, le=2100)
    mass_kg: float | None = Field(default=None, gt=0)
    length_m: float | None = Field(default=None, gt=0)
    width_m: float | None = Field(default=None, gt=0)
    wheelbase_m: float | None = Field(default=None, gt=0)
    data_source: str = Field(min_length=3, description="Where the vehicle data came from")
    provenance: dict[str, Any] = Field(description="Provenance contract")
    notes: str = ""


class VehicleOut(ORM):
    id: str
    case_id: str
    label: str
    make: str | None
    model: str | None
    year: int | None
    mass_kg: float | None
    length_m: float | None
    width_m: float | None
    wheelbase_m: float | None
    data_source: str
    provenance: dict[str, Any]
    notes: str
    created_at: datetime


class WitnessIn(BaseModel):
    reference: str = Field(min_length=1, max_length=120)
    summary: str = ""
    obtained_at: datetime | None = None
    evidence_item_id: str | None = Field(default=None, description="Evidence item holding the statement document")


class WitnessOut(ORM):
    id: str
    case_id: str
    reference: str
    summary: str
    obtained_at: datetime | None
    source_asset_id: str | None
    created_at: datetime


class TimelineIn(BaseModel):
    t_offset_s: float | None = None
    at: datetime | None = None
    t_uncertainty_s: float | None = Field(default=None, ge=0)
    description: str = Field(min_length=3, max_length=2000)
    source_type: Literal["evidence", "witness", "record", "hypothesis"]
    source_id: str | None = None
    hypothesis_id: str | None = None


class TimelineOut(ORM):
    id: str
    case_id: str
    t_offset_s: float | None
    at: datetime | None
    t_uncertainty_s: float | None
    description: str
    source_type: str
    source_id: str | None
    hypothesis_id: str | None
    created_at: datetime


class HypothesisIn(BaseModel):
    label: str = Field(min_length=2, max_length=200)
    content: dict[str, Any] = Field(description="HypothesisContent")


class HypothesisPatch(BaseModel):
    label: str | None = Field(default=None, min_length=2, max_length=200)
    content: dict[str, Any] | None = None
    status: Literal["active", "set_aside", "retained"] | None = None


class LinkIn(BaseModel):
    observation_id: str
    relation: Literal["supports", "contradicts", "context"]
    rationale: str = Field(min_length=3, max_length=2000)


class LinkOut(ORM):
    id: str
    hypothesis_id: str
    observation_id: str
    relation: str
    rationale: str
    created_by: str | None
    created_at: datetime


class HypothesisOut(ORM):
    id: str
    case_id: str
    label: str
    content: dict[str, Any]
    status: str
    created_by: str | None
    created_at: datetime
    links: list[LinkOut] = Field(default_factory=list)


class SkidSpeedIn(BaseModel):
    observation_id: str = Field(description="A skid-mark observation with a measured length in metres")
    friction_low: float = Field(gt=0, le=1.5)
    friction_high: float = Field(gt=0, le=1.5)
    friction_basis: str = Field(min_length=3, description="Source of the friction range")


class AnalysisIn(BaseModel):
    run_id: str


class CollisionTargetIn(BaseModel):
    observation_id: str = Field(description="A located observation with an absolute position uncertainty")
    kind: Literal["rest_position", "point_of_impact_area", "tyre_mark_start", "tyre_mark_end", "debris_centroid"]
    vehicle_record_id: str | None = None
    heading_deg: float | None = Field(default=None, description="Measured rest heading, if recorded")


class CollisionModelIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    scene_revision_id: str | None = None
    friction_low: float = Field(gt=0, le=1.5)
    friction_high: float = Field(gt=0, le=1.5)
    friction_basis: str = Field(min_length=3)
    vehicle_record_ids: list[str] = Field(min_length=1, max_length=8)
    targets: list[CollisionTargetIn] = Field(default_factory=list, max_length=40)
    road_edge_observation_ids: list[str] = Field(
        default_factory=list, description="Observations whose LineString geometry traces a road edge"
    )
