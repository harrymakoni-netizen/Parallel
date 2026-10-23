"""Revision rules (spec section 11.3).

Published revisions are immutable; edits create new revisions; runs reference
exact revision ids. Immutability is enforced here and by a database trigger
created in the initial migration.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.scenario import ScenarioContent
from parallel_contracts.scene import SceneRevisionContent
from parallel_engines.base import sha256_json

from .db import utcnow
from .models import (
    Annotation,
    DomainModel,
    DomainModelRevision,
    ReferenceMeasurement,
    Scenario,
    ScenarioRevision,
    Scene,
    SceneRevision,
    SpatialObject,
)


class RevisionError(ValueError):
    pass


def _next_number(db: Session, model, fk_col, parent_id: str) -> int:
    return (db.scalar(select(func.max(model.number)).where(fk_col == parent_id)) or 0) + 1


def _mutable(rev) -> None:
    if rev.status == "published":
        raise RevisionError(
            f"{type(rev).__name__} {rev.id} is published and immutable; create a new revision"
        )


# ---------------------------------------------------------------- scenes
def create_scene_revision(
    db: Session,
    scene: Scene,
    content: SceneRevisionContent,
    user_id: str | None,
    message: str = "",
    parent_id: str | None = None,
) -> SceneRevision:
    number = _next_number(db, SceneRevision, SceneRevision.scene_id, scene.id)
    content = content.model_copy(update={"scene_id": scene.id, "revision_number": number})
    data = content.model_dump(mode="json")
    rev = SceneRevision(
        scene_id=scene.id,
        number=number,
        status="draft",
        content=data,
        content_sha256=sha256_json(data),
        parent_revision_id=parent_id,
        message=message,
        created_by=user_id,
    )
    db.add(rev)
    db.flush()
    _materialise_scene(db, rev, content)
    scene.current_revision_id = rev.id
    return rev


def update_scene_draft(db: Session, rev: SceneRevision, content: SceneRevisionContent) -> SceneRevision:
    _mutable(rev)
    content = content.model_copy(update={"scene_id": rev.scene_id, "revision_number": rev.number})
    data = content.model_dump(mode="json")
    rev.content = data
    rev.content_sha256 = sha256_json(data)
    for model in (SpatialObject, Annotation, ReferenceMeasurement):
        db.query(model).filter(model.scene_revision_id == rev.id).delete()
    _materialise_scene(db, rev, content)
    return rev


def _materialise_scene(db: Session, rev: SceneRevision, content: SceneRevisionContent) -> None:
    from shapely.geometry import shape

    from parallel_scene.geometry import to_shapely

    for o in content.spatial_objects:
        g = o.geometry.model_dump()
        wkt = None
        try:
            if g["type"] in ("Polygon", "MultiPolygon"):
                wkt = to_shapely(o.geometry).wkt
            elif g["type"] in ("Point", "LineString"):
                wkt = shape(g).wkt
        except Exception:  # invalid geometry is reported by validation, not stored spatially
            wkt = None
        db.add(
            SpatialObject(
                scene_revision_id=rev.id,
                key=o.id,
                name=o.name,
                kind=o.kind,
                layer=o.layer,
                belongs_to=o.belongs_to.value,
                embedded_in_capture=o.embedded_in_capture,
                geom=f"SRID=0;{wkt}" if wkt else None,
            )
        )
    for a in content.annotations:
        db.add(
            Annotation(scene_revision_id=rev.id, key=a.id, title=a.title, kind=a.kind, position=a.position)
        )
    for m in content.measurements:
        ref = None
        if m.reference_distance is not None:
            from parallel_contracts.units import convert

            ref = convert(m.reference_distance.value, m.reference_distance.unit, "m")
        db.add(
            ReferenceMeasurement(
                scene_revision_id=rev.id,
                key=m.id,
                label=m.label,
                purpose=m.purpose,
                reference_m=ref,
                reconstructed_m=m.reconstructed_distance_m,
                abs_error_m=m.abs_error_m,
                rel_error=m.rel_error,
                within_tolerance=m.within_tolerance,
                source=m.source,
            )
        )


def publish(db: Session, rev: Any) -> Any:
    _mutable(rev)
    rev.status = "published"
    rev.published_at = utcnow()
    return rev


# ---------------------------------------------------------------- domain models
def create_model_revision(
    db: Session,
    dm: DomainModel,
    envelope: DomainModelEnvelope,
    user_id: str | None,
    validation: dict,
    message: str = "",
    parent_id: str | None = None,
) -> DomainModelRevision:
    number = _next_number(db, DomainModelRevision, DomainModelRevision.domain_model_id, dm.id)
    data = envelope.model_dump(mode="json")
    rev = DomainModelRevision(
        domain_model_id=dm.id,
        number=number,
        status="draft",
        content=data,
        content_sha256=sha256_json(data),
        validation=validation,
        message=message,
        parent_revision_id=parent_id,
        created_by=user_id,
    )
    db.add(rev)
    db.flush()
    dm.current_revision_id = rev.id
    return rev


def update_model_draft(
    rev: DomainModelRevision, envelope: DomainModelEnvelope, validation: dict
) -> DomainModelRevision:
    _mutable(rev)
    data = envelope.model_dump(mode="json")
    rev.content = data
    rev.content_sha256 = sha256_json(data)
    rev.validation = validation
    return rev


# ---------------------------------------------------------------- scenarios
def create_scenario_revision(
    db: Session,
    sc: Scenario,
    content: ScenarioContent,
    user_id: str | None,
    *,
    origin: str = "manual",
    message: str = "",
    parent_id: str | None = None,
) -> ScenarioRevision:
    number = _next_number(db, ScenarioRevision, ScenarioRevision.scenario_id, sc.id)
    data = content.model_dump(mode="json")
    rev = ScenarioRevision(
        scenario_id=sc.id,
        number=number,
        status="draft",
        content=data,
        content_sha256=sha256_json(data),
        origin=origin,
        message=message,
        parent_revision_id=parent_id,
        created_by=user_id,
    )
    if origin == "manual":
        rev.adopted_by = user_id
        rev.adopted_at = utcnow()
    db.add(rev)
    db.flush()
    sc.current_revision_id = rev.id
    return rev


def update_scenario_draft(rev: ScenarioRevision, content: ScenarioContent) -> ScenarioRevision:
    _mutable(rev)
    data = content.model_dump(mode="json")
    rev.content = data
    rev.content_sha256 = sha256_json(data)
    return rev


def adopt(rev: ScenarioRevision, user_id: str) -> ScenarioRevision:
    """Explicit user action turning an AI-drafted scenario into an adoptable model input.

    Every AI-suggested value inside the draft records the adopting user, so the run
    gate (which refuses unadopted AI values) accepts it from now on.
    """
    import copy

    from parallel_contracts.provenance import mark_ai_adopted

    now = utcnow()
    if rev.status == "draft":
        data = copy.deepcopy(rev.content)
        if mark_ai_adopted(data, user_id, now):
            rev.content = data
            rev.content_sha256 = sha256_json(data)
    rev.adopted_by = user_id
    rev.adopted_at = now
    return rev


IMMUTABILITY_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION parallel_forbid_published_update() RETURNS trigger AS $$
BEGIN
  IF OLD.status = 'published' AND (NEW.content IS DISTINCT FROM OLD.content OR NEW.status <> 'published') THEN
    RAISE EXCEPTION 'published revisions are immutable (%: %)', TG_TABLE_NAME, OLD.id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""


def trigger_sql(table: str) -> str:
    return (
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION parallel_forbid_published_update();"
    )
