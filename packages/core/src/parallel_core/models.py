"""Persistent data model (spec section 11).

Structured records and references live in PostgreSQL (with PostGIS for spatial
columns); large binaries live in object storage and are referenced by key.
Revision content is stored as validated JSON snapshots; published revisions
are immutable (enforced in the service layer and by a database trigger).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from geoalchemy2 import Geography, Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, IdMixin, TimestampMixin

J = JSONB


def fk(target: str, ondelete: str = "CASCADE", nullable: bool = False, index: bool = True):
    return mapped_column(ForeignKey(target, ondelete=ondelete), nullable=nullable, index=index)


# ============================================================ organisations, users, access
class Organisation(IdMixin, TimestampMixin, Base):
    __tablename__ = "organisation"
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    settings: Mapped[dict[str, Any]] = mapped_column(J, default=dict)  # AI data policy, quotas


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "app_user"
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(300))
    oidc_subject: Mapped[str | None] = mapped_column(String(300), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class OrganisationMembership(IdMixin, TimestampMixin, Base):
    __tablename__ = "organisation_membership"
    __table_args__ = (UniqueConstraint("organisation_id", "user_id"),)
    organisation_id: Mapped[str] = fk("organisation.id")
    user_id: Mapped[str] = fk("app_user.id")
    role: Mapped[str] = mapped_column(String(20))  # admin | member


class Project(IdMixin, TimestampMixin, Base):
    __tablename__ = "project"
    organisation_id: Mapped[str] = fk("organisation.id")
    name: Mapped[str] = mapped_column(String(200))
    project_type: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[Any | None] = mapped_column(Geography("POINT", srid=4326), nullable=True)
    location_label: Mapped[str | None] = mapped_column(String(300))
    georeference: Mapped[dict | None] = mapped_column(J)
    demo_key: Mapped[str | None] = mapped_column(String(60), unique=True)
    template: Mapped[str | None] = mapped_column(String(60))
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectMembership(IdMixin, TimestampMixin, Base):
    __tablename__ = "project_membership"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    project_id: Mapped[str] = fk("project.id")
    user_id: Mapped[str] = fk("app_user.id")
    role: Mapped[str] = mapped_column(String(20))  # editor | reviewer | viewer


# ============================================================ capture and assets
class CaptureBatch(IdMixin, TimestampMixin, Base):
    __tablename__ = "capture_batch"
    project_id: Mapped[str] = fk("project.id")
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(
        String(30)
    )  # photos | video | drone | gaussian | point_cloud | mesh | terrain | model_files
    notes: Mapped[str] = mapped_column(Text, default="")
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class SourceAsset(IdMixin, TimestampMixin, Base):
    __tablename__ = "source_asset"
    project_id: Mapped[str] = fk("project.id")
    capture_batch_id: Mapped[str | None] = fk("capture_batch.id", ondelete="SET NULL", nullable=True)
    kind: Mapped[str] = mapped_column(
        String(30)
    )  # image | video | gaussian | point_cloud | mesh | terrain | weather | model | document | other
    filename: Mapped[str] = mapped_column(String(400))
    media_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(600), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="uploading")  # uploading | verified | rejected
    inspection: Mapped[dict] = mapped_column(J, default=dict)
    sensitivity: Mapped[str] = mapped_column(String(20), default="normal")  # normal | evidence
    derived_from_id: Mapped[str | None] = fk("source_asset.id", ondelete="SET NULL", nullable=True)
    uploaded_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class ReconstructionJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "reconstruction_job"
    project_id: Mapped[str] = fk("project.id")
    capture_batch_id: Mapped[str] = fk("capture_batch.id")
    scene_id: Mapped[str | None] = fk("scene.id", ondelete="SET NULL", nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    stages: Mapped[list] = mapped_column(J, default=list)
    settings: Mapped[dict] = mapped_column(J, default=dict)
    result_scene_revision_id: Mapped[str | None] = mapped_column(String(32))
    celery_task_id: Mapped[str | None] = mapped_column(String(80))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


# ============================================================ scenes
class Scene(IdMixin, TimestampMixin, Base):
    __tablename__ = "scene"
    project_id: Mapped[str] = fk("project.id")
    name: Mapped[str] = mapped_column(String(200))
    current_revision_id: Mapped[str | None] = mapped_column(String(32))


class SceneRevision(IdMixin, TimestampMixin, Base):
    __tablename__ = "scene_revision"
    __table_args__ = (UniqueConstraint("scene_id", "number"),)
    scene_id: Mapped[str] = fk("scene.id")
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | published
    content: Mapped[dict] = mapped_column(J)
    content_sha256: Mapped[str] = mapped_column(String(64))
    parent_revision_id: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferenceMeasurement(IdMixin, TimestampMixin, Base):
    """Materialised from scene revision content for querying; the revision snapshot is canonical."""

    __tablename__ = "reference_measurement"
    scene_revision_id: Mapped[str] = fk("scene_revision.id")
    key: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(30))
    reference_m: Mapped[float | None] = mapped_column(Float)
    reconstructed_m: Mapped[float | None] = mapped_column(Float)
    abs_error_m: Mapped[float | None] = mapped_column(Float)
    rel_error: Mapped[float | None] = mapped_column(Float)
    within_tolerance: Mapped[bool | None] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(300))


class SpatialObject(IdMixin, TimestampMixin, Base):
    """Materialised from scene revision content; ``geom`` (SRID 0, local metres) supports spatial queries."""

    __tablename__ = "spatial_object"
    scene_revision_id: Mapped[str] = fk("scene_revision.id")
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(60))
    layer: Mapped[str] = mapped_column(String(60))
    belongs_to: Mapped[str] = mapped_column(String(30))
    embedded_in_capture: Mapped[bool] = mapped_column(Boolean, default=False)
    geom: Mapped[Any | None] = mapped_column(Geometry(geometry_type="GEOMETRY", srid=0), nullable=True)


class Annotation(IdMixin, TimestampMixin, Base):
    __tablename__ = "annotation"
    scene_revision_id: Mapped[str] = fk("scene_revision.id")
    key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(30))
    position: Mapped[list] = mapped_column(J)


# ============================================================ domain models and scenarios
class DomainModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "domain_model"
    project_id: Mapped[str] = fk("project.id")
    # set for CrashLens analysis models: visible only to people assigned to that case
    case_id: Mapped[str | None] = fk("crash_case.id", ondelete="RESTRICT", nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    discipline: Mapped[str] = mapped_column(String(40))
    current_revision_id: Mapped[str | None] = mapped_column(String(32))


class DomainModelRevision(IdMixin, TimestampMixin, Base):
    __tablename__ = "domain_model_revision"
    __table_args__ = (UniqueConstraint("domain_model_id", "number"),)
    domain_model_id: Mapped[str] = fk("domain_model.id")
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    content: Mapped[dict] = mapped_column(J)
    content_sha256: Mapped[str] = mapped_column(String(64))
    validation: Mapped[dict] = mapped_column(J, default=dict)
    parent_revision_id: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Scenario(IdMixin, TimestampMixin, Base):
    __tablename__ = "scenario"
    project_id: Mapped[str] = fk("project.id")
    case_id: Mapped[str | None] = fk("crash_case.id", ondelete="RESTRICT", nullable=True)
    domain_model_id: Mapped[str] = fk("domain_model.id")
    name: Mapped[str] = mapped_column(String(200))
    engine_id: Mapped[str] = mapped_column(String(40))
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(32))


class ScenarioRevision(IdMixin, TimestampMixin, Base):
    __tablename__ = "scenario_revision"
    __table_args__ = (UniqueConstraint("scenario_id", "number"),)
    scenario_id: Mapped[str] = fk("scenario.id")
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    content: Mapped[dict] = mapped_column(J)
    content_sha256: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(20), default="manual")  # manual | ai | optimisation
    adopted_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parent_revision_id: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ============================================================ engines
class EngineCapabilityRecord(Base):
    __tablename__ = "engine_capability"
    engine_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    adapter_version: Mapped[str] = mapped_column(String(20))
    record: Mapped[dict] = mapped_column(J)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EngineInstallation(IdMixin, TimestampMixin, Base):
    __tablename__ = "engine_installation"
    organisation_id: Mapped[str | None] = fk("organisation.id", nullable=True)
    engine_id: Mapped[str] = mapped_column(String(40), index=True)
    execution_mode: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(200))
    edition: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[str | None] = mapped_column(String(80))
    licence_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    licence_holder: Mapped[str | None] = mapped_column(String(200))
    operator_contact: Mapped[str | None] = mapped_column(String(200))
    worker: Mapped[str | None] = mapped_column(String(120))
    last_check: Mapped[dict] = mapped_column(J, default=dict)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


# ============================================================ runs and results
class SimulationRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "simulation_run"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key"),
        Index("ix_simulation_run_state_heartbeat", "state", "heartbeat_at"),
    )
    project_id: Mapped[str] = fk("project.id")
    case_id: Mapped[str | None] = fk("crash_case.id", ondelete="RESTRICT", nullable=True)
    scenario_id: Mapped[str] = fk("scenario.id")
    scenario_revision_id: Mapped[str] = fk("scenario_revision.id", ondelete="RESTRICT")
    domain_model_revision_id: Mapped[str] = fk("domain_model_revision.id", ondelete="RESTRICT")
    scene_revision_id: Mapped[str | None] = fk("scene_revision.id", ondelete="RESTRICT", nullable=True)
    engine_id: Mapped[str] = mapped_column(String(40), index=True)
    execution_mode: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(30), default="draft")
    state_history: Mapped[list] = mapped_column(J, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    label: Mapped[str | None] = mapped_column(String(200))
    seed: Mapped[int | None] = mapped_column(BigInteger)
    settings: Mapped[dict] = mapped_column(J, default=dict)
    validation: Mapped[dict] = mapped_column(J, default=dict)
    resource_estimate: Mapped[dict] = mapped_column(J, default=dict)
    model_change_notes: Mapped[list] = mapped_column(J, default=list)
    celery_task_id: Mapped[str | None] = mapped_column(String(80))
    worker: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[float | None] = mapped_column(Float)
    progress_message: Mapped[str | None] = mapped_column(String(500))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_s: Mapped[int] = mapped_column(Integer, default=1800)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    engine_version: Mapped[str | None] = mapped_column(String(200))
    adapter_version: Mapped[str | None] = mapped_column(String(20))
    environment: Mapped[dict] = mapped_column(J, default=dict)
    input_hashes: Mapped[dict] = mapped_column(J, default=dict)
    result_summary: Mapped[dict] = mapped_column(J, default=dict)
    result_envelope_key: Mapped[str | None] = mapped_column(String(600))
    warnings: Mapped[list] = mapped_column(J, default=list)
    optimisation_study_id: Mapped[str | None] = fk(
        "optimisation_study.id", ondelete="SET NULL", nullable=True
    )
    exchange_package_key: Mapped[str | None] = mapped_column(String(600))
    imported_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_installation: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)

    artifacts: Mapped[list[RunArtifact]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    metrics: Mapped[list[Metric]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class RunArtifact(IdMixin, TimestampMixin, Base):
    __tablename__ = "run_artifact"
    run_id: Mapped[str] = fk("simulation_run.id")
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(30))
    media_type: Mapped[str] = mapped_column(String(120))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(600))
    description: Mapped[str | None] = mapped_column(Text)
    run: Mapped[SimulationRun] = relationship(back_populates="artifacts")


class Metric(IdMixin, Base):
    __tablename__ = "metric"
    run_id: Mapped[str] = fk("simulation_run.id")
    metric_id: Mapped[str] = mapped_column(String(80), index=True)
    entity: Mapped[str | None] = mapped_column(String(120))
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict | None] = mapped_column(J)
    replicate: Mapped[int | None] = mapped_column(Integer)
    run: Mapped[SimulationRun] = relationship(back_populates="metrics")


class Comparison(IdMixin, TimestampMixin, Base):
    __tablename__ = "comparison"
    project_id: Mapped[str] = fk("project.id")
    name: Mapped[str] = mapped_column(String(200))
    baseline_run_id: Mapped[str] = fk("simulation_run.id", ondelete="RESTRICT")
    run_ids: Mapped[list] = mapped_column(J)
    compatibility: Mapped[dict] = mapped_column(J, default=dict)
    table: Mapped[list] = mapped_column(J, default=list)
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class OptimisationStudy(IdMixin, TimestampMixin, Base):
    __tablename__ = "optimisation_study"
    project_id: Mapped[str] = fk("project.id")
    name: Mapped[str] = mapped_column(String(200))
    base_scenario_revision_id: Mapped[str] = fk("scenario_revision.id", ondelete="RESTRICT")
    definition: Mapped[dict] = mapped_column(
        J
    )  # objective, variables, bounds, constraints, budget, method, stopping
    status: Mapped[str] = mapped_column(String(20), default="queued")
    candidates: Mapped[list] = mapped_column(J, default=list)
    best_candidate_index: Mapped[int | None] = mapped_column(Integer)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    celery_task_id: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Report(IdMixin, TimestampMixin, Base):
    __tablename__ = "report"
    project_id: Mapped[str] = fk("project.id")
    kind: Mapped[str] = mapped_column(String(20))  # project | comparison | case
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    run_ids: Mapped[list] = mapped_column(J, default=list)
    comparison_id: Mapped[str | None] = fk("comparison.id", ondelete="SET NULL", nullable=True)
    case_id: Mapped[str | None] = fk("crash_case.id", ondelete="SET NULL", nullable=True)
    review_status: Mapped[str] = mapped_column(String(30), default="not reviewed")
    narrative: Mapped[str | None] = mapped_column(Text)
    narrative_origin: Mapped[str | None] = mapped_column(String(20))  # manual | ai_draft_adopted
    manifest: Mapped[dict] = mapped_column(J, default=dict)
    pdf_key: Mapped[str | None] = mapped_column(String(600))
    csv_key: Mapped[str | None] = mapped_column(String(600))
    json_key: Mapped[str | None] = mapped_column(String(600))
    error: Mapped[str | None] = mapped_column(Text)
    author_id: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewRecord(IdMixin, TimestampMixin, Base):
    __tablename__ = "review_record"
    project_id: Mapped[str] = fk("project.id")
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String(32), index=True)
    scope: Mapped[str] = mapped_column(
        String(40)
    )  # geometry | evidence_annotations | model_assumptions | interpretation | report
    status: Mapped[str] = mapped_column(String(30))  # approved | changes_requested | commented
    comment: Mapped[str] = mapped_column(Text, default="")
    reviewer_id: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class AuditEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "audit_event"
    organisation_id: Mapped[str | None] = fk("organisation.id", ondelete="SET NULL", nullable=True)
    project_id: Mapped[str | None] = fk("project.id", ondelete="SET NULL", nullable=True)
    actor_id: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(J, default=dict)


# ============================================================ CrashLens
class Case(IdMixin, TimestampMixin, Base):
    __tablename__ = "crash_case"
    project_id: Mapped[str] = fk("project.id")
    reference: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="open")
    incident_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location_note: Mapped[str] = mapped_column(Text, default="")
    training: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class CaseAssignment(IdMixin, TimestampMixin, Base):
    __tablename__ = "case_assignment"
    __table_args__ = (UniqueConstraint("case_id", "user_id"),)
    case_id: Mapped[str] = fk("crash_case.id")
    user_id: Mapped[str] = fk("app_user.id")
    role: Mapped[str] = mapped_column(String(20))  # investigator | reviewer
    assigned_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class EvidenceItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "evidence_item"
    case_id: Mapped[str] = fk("crash_case.id")
    source_asset_id: Mapped[str | None] = fk("source_asset.id", ondelete="RESTRICT", nullable=True)
    kind: Mapped[str] = mapped_column(
        String(30)
    )  # image | video | document | vehicle_data | measurement | other
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    claimed_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    captured_by: Mapped[str | None] = mapped_column(String(200))
    source_metadata: Mapped[dict] = mapped_column(J, default=dict)
    original_sha256: Mapped[str | None] = mapped_column(String(64))
    received_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class EvidenceDerivative(IdMixin, TimestampMixin, Base):
    __tablename__ = "evidence_derivative"
    evidence_item_id: Mapped[str] = fk("evidence_item.id")
    kind: Mapped[str] = mapped_column(
        String(30)
    )  # redaction | enhancement | annotation_overlay | crop | frame_extract
    source_sha256: Mapped[str] = mapped_column(String(64))
    derived_asset_id: Mapped[str] = fk("source_asset.id", ondelete="RESTRICT")
    method: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class EvidenceObservation(IdMixin, TimestampMixin, Base):
    __tablename__ = "evidence_observation"
    case_id: Mapped[str] = fk("crash_case.id")
    evidence_item_id: Mapped[str | None] = fk("evidence_item.id", ondelete="SET NULL", nullable=True)
    statement_type: Mapped[str] = mapped_column(String(20))  # observation | interpretation
    kind: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[list | None] = mapped_column(J)
    geometry: Mapped[dict | None] = mapped_column(J)
    measurement_value: Mapped[float | None] = mapped_column(Float)
    measurement_unit: Mapped[str | None] = mapped_column(String(20))
    uncertainty: Mapped[dict | None] = mapped_column(J)
    limitation: Mapped[str] = mapped_column(Text, default="")
    based_on: Mapped[list] = mapped_column(
        J, default=list, server_default="[]"
    )  # interpretations: the observation ids they reason from
    review_status: Mapped[str] = mapped_column(String(20), default="unreviewed")
    author_id: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class VehicleRecord(IdMixin, TimestampMixin, Base):
    __tablename__ = "vehicle_record"
    case_id: Mapped[str] = fk("crash_case.id")
    label: Mapped[str] = mapped_column(String(200))
    make: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))
    year: Mapped[int | None] = mapped_column(Integer)
    mass_kg: Mapped[float | None] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    width_m: Mapped[float | None] = mapped_column(Float)
    wheelbase_m: Mapped[float | None] = mapped_column(Float)
    data_source: Mapped[str] = mapped_column(String(300), default="")
    provenance: Mapped[dict] = mapped_column(J, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")


class WitnessStatementReference(IdMixin, TimestampMixin, Base):
    __tablename__ = "witness_statement_reference"
    case_id: Mapped[str] = fk("crash_case.id")
    reference: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(Text, default="")
    obtained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_asset_id: Mapped[str | None] = fk("source_asset.id", ondelete="SET NULL", nullable=True)


class Hypothesis(IdMixin, TimestampMixin, Base):
    __tablename__ = "hypothesis"
    case_id: Mapped[str] = fk("crash_case.id")
    label: Mapped[str] = mapped_column(String(200))
    content: Mapped[dict] = mapped_column(J)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class HypothesisEvidenceLink(IdMixin, TimestampMixin, Base):
    __tablename__ = "hypothesis_evidence_link"
    __table_args__ = (UniqueConstraint("hypothesis_id", "observation_id"),)
    hypothesis_id: Mapped[str] = fk("hypothesis.id")
    observation_id: Mapped[str] = fk("evidence_observation.id")
    relation: Mapped[str] = mapped_column(String(20))  # supports | contradicts | context
    rationale: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class TimelineEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "timeline_event"
    case_id: Mapped[str] = fk("crash_case.id")
    t_offset_s: Mapped[float | None] = mapped_column(Float)
    at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    t_uncertainty_s: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20))  # evidence | witness | record | hypothesis
    source_id: Mapped[str | None] = mapped_column(String(32))
    hypothesis_id: Mapped[str | None] = fk("hypothesis.id", ondelete="CASCADE", nullable=True)
    created_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


class EvidenceHandlingEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "evidence_handling_event"
    evidence_item_id: Mapped[str] = fk("evidence_item.id")
    action: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)
    details: Mapped[dict] = mapped_column(J, default=dict)


class ExternalAnalysisRecord(IdMixin, TimestampMixin, Base):
    __tablename__ = "external_analysis_record"
    case_id: Mapped[str] = fk("crash_case.id")
    hypothesis_id: Mapped[str | None] = fk("hypothesis.id", ondelete="SET NULL", nullable=True)
    run_id: Mapped[str | None] = fk("simulation_run.id", ondelete="SET NULL", nullable=True)
    engine_id: Mapped[str] = mapped_column(String(40))
    external_installation: Mapped[str] = mapped_column(String(300))
    analyst: Mapped[str] = mapped_column(String(200))
    method: Mapped[str] = mapped_column(Text)
    supplied_provenance: Mapped[str] = mapped_column(Text)
    package_sha256: Mapped[str | None] = mapped_column(String(64))
    imported_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)


# ============================================================ AI assistant
class AssistantSession(IdMixin, TimestampMixin, Base):
    __tablename__ = "assistant_session"
    project_id: Mapped[str] = fk("project.id")
    user_id: Mapped[str] = fk("app_user.id")
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    prompt_template_version: Mapped[str] = mapped_column(String(20))


class AssistantMessage(IdMixin, TimestampMixin, Base):
    __tablename__ = "assistant_message"
    session_id: Mapped[str] = fk("assistant_session.id")
    seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # replay order
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[list | dict | str] = mapped_column(J)
    usage: Mapped[dict] = mapped_column(J, default=dict)


class AssistantToolCall(IdMixin, TimestampMixin, Base):
    __tablename__ = "assistant_tool_call"
    session_id: Mapped[str] = fk("assistant_session.id")
    seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tool_use_id: Mapped[str | None] = mapped_column(String(80))
    # suggestions (scenario drafts, report narratives) become inputs only through explicit adoption
    adopted_by: Mapped[str | None] = fk("app_user.id", ondelete="SET NULL", nullable=True)
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    adopted_object_id: Mapped[str | None] = mapped_column(String(32))
    tool_name: Mapped[str] = mapped_column(String(60))
    arguments: Mapped[dict] = mapped_column(J)
    result: Mapped[dict] = mapped_column(J, default=dict)
    authorised: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    source_refs: Mapped[list] = mapped_column(J, default=list)
