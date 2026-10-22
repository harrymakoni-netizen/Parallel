"""Scene revision contract (spec section 12.3).

A scene revision separates:
* visual assets (Gaussian splats, point clouds, meshes, images),
* spatial objects (measured geometry, object locations),
* measurements and annotations,
* references to domain models,
* assumptions, quality and provenance.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from . import SCHEMA_VERSION
from .coordinates import SceneCoordinates
from .geometry import Geometry, Position
from .provenance import Assumption, Provenance, ProvenanceKind, SourcedQuantity, SourcedValue
from .quality import QualityAssessment


class Belonging(StrEnum):
    """Whether an object/property belongs to the recorded scene, a prepared model, or a hypothetical scenario."""

    recorded_scene = "recorded_scene"
    prepared_model = "prepared_model"
    hypothetical_scenario = "hypothetical_scenario"


class VisualAssetKind(StrEnum):
    gaussian_splat = "gaussian_splat"
    gaussian_initialisation = "gaussian_initialisation"  # untrained SfM-initialised Gaussians
    point_cloud = "point_cloud"
    mesh = "mesh"
    collision_mesh = "collision_mesh"
    image_set = "image_set"
    terrain = "terrain"


class VisualAsset(BaseModel):
    id: str
    asset_id: str = Field(description="Stored file (SourceAsset or RunArtifact) holding the visual data.")
    kind: VisualAssetKind
    format: str = Field(description="ply | splat | spz | ksplat | obj | glb | tif | ...")
    frame: str = Field(default="canonical", description="Frame the asset's coordinates are expressed in.")
    label: str | None = None
    generated: bool = Field(
        default=False,
        description="True for generated (non-captured) content. Only allowed in hypothetical scenes.",
    )
    quality_notes: list[str] = Field(default_factory=list)
    provenance: Provenance | None = None


class SpatialObject(BaseModel):
    id: str
    name: str
    kind: str = Field(description="e.g. exit, obstacle, walkable_area, vehicle, equipment, zone, evidence_marker")
    layer: str = "objects"
    geometry: Geometry
    belongs_to: Belonging = Belonging.prepared_model
    embedded_in_capture: bool = Field(
        default=False,
        description="True when the object's appearance is baked into the captured reconstruction.",
    )
    captured_geometry: Geometry | None = Field(
        default=None,
        description="Where the object appears in the capture, when a scenario has moved it.",
    )
    properties: dict[str, SourcedValue] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))


class MeasurementTolerance(BaseModel):
    purpose: str = Field(description="What the tolerance is fit for, e.g. 'room-scale demonstration'.")
    max_abs_error_m: float | None = None
    max_rel_error: float | None = None
    not_suitable_for: list[str] = Field(
        default_factory=lambda: ["surveying", "drainage design", "forensic measurement"]
    )


class Measurement(BaseModel):
    id: str
    label: str
    purpose: Literal["scale_reference", "independent_check", "annotation"]
    point_a: Position
    point_b: Position
    frame: str = "canonical"
    reference_distance: SourcedQuantity | None = None
    reconstructed_distance_m: float | None = None
    abs_error_m: float | None = None
    rel_error: float | None = None
    within_tolerance: bool | None = None
    source: str = Field(description="Measurement source (instrument, survey, picked points, ...).")
    created_by: str | None = None
    created_at: datetime | None = None


class Annotation(BaseModel):
    id: str
    position: Position
    title: str
    body: str = ""
    kind: Literal["note", "issue", "evidence", "viewpoint"] = "note"
    author: str | None = None
    created_at: datetime | None = None
    linked_ids: list[str] = Field(default_factory=list)


class Viewpoint(BaseModel):
    id: str
    name: str
    position: Position
    target: Position
    up: Position = Field(default_factory=lambda: [0.0, 0.0, 1.0])


class SceneRevisionContent(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION  # type: ignore[assignment]
    scene_id: str
    revision_number: int = 1
    hypothetical: bool = Field(
        default=False, description="Explicitly labelled hypothetical environment (may contain generated content)."
    )
    coordinates: SceneCoordinates = Field(default_factory=SceneCoordinates)
    source_asset_ids: list[str] = Field(default_factory=list)
    visual_assets: list[VisualAsset] = Field(default_factory=list)
    spatial_objects: list[SpatialObject] = Field(default_factory=list)
    measurements: list[Measurement] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    viewpoints: list[Viewpoint] = Field(default_factory=list)
    domain_model_refs: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    tolerance: MeasurementTolerance | None = None
    quality: QualityAssessment = Field(default_factory=QualityAssessment)
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.imported))

    @model_validator(mode="after")
    def _generated_only_in_hypothetical(self) -> SceneRevisionContent:
        if not self.hypothetical:
            gen = [v.id for v in self.visual_assets if v.generated]
            if gen:
                raise ValueError(
                    "Generated visual content cannot be inserted into a captured (non-hypothetical) scene: "
                    + ", ".join(gen)
                )
            hyp_recorded = [
                o.id
                for o in self.spatial_objects
                if o.belongs_to == Belonging.recorded_scene and o.provenance.kind == ProvenanceKind.ai_suggested
            ]
            if hyp_recorded:
                raise ValueError(
                    "AI-suggested objects cannot be recorded as observed scene content: " + ", ".join(hyp_recorded)
                )
        ids = [o.id for o in self.spatial_objects]
        if len(ids) != len(set(ids)):
            raise ValueError("spatial object ids must be unique")
        return self
