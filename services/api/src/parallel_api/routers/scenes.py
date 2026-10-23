"""Scenes, scene revisions, measurements, alignment and model preparation (/api/v1/scenes)."""

from __future__ import annotations

from typing import Literal

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from parallel_contracts.coordinates import CoordinateFrame, FrameKind, SceneCoordinates, Transform
from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.common import NamedPolygon
from parallel_contracts.domain.pedestrian import ExitDef, PedestrianModel, RouteSegment, StartGroup
from parallel_contracts.geometry import MultiPolygonGeom, PolygonGeom
from parallel_contracts.provenance import Provenance, ProvenanceKind, SourcedQuantity
from parallel_contracts.quality import QUALITY_LABELS, QualityAssessment, QualityState
from parallel_contracts.scene import Measurement, SceneRevisionContent, VisualAsset
from parallel_core.access import Action, require_project, visible_case_ids
from parallel_core.audit import record as audit
from parallel_core.db import utcnow
from parallel_core.models import (
    DomainModel,
    DomainModelRevision,
    ReviewRecord,
    Scene,
    SceneRevision,
    SimulationRun,
    SourceAsset,
    SpatialObject,
)
from parallel_core.revisions import create_scene_revision, publish, update_scene_draft
from parallel_scene.geometry import validate_polygon
from parallel_scene.measurement import evaluate
from parallel_scene.transforms import FrameGraph, apply, scale_from_reference, similarity, to_list, umeyama

from ..deps import DB, CurrentUser
from ..errors import ApiError
from ..schemas import RevisionIn, RevisionOut

router = APIRouter(tags=["scenes"])

VISUAL_KIND = {"gaussian": "gaussian_splat", "point_cloud": "point_cloud", "mesh": "mesh", "terrain": "terrain"}


class SceneIn(BaseModel):
    name: str
    visual_asset_ids: list[str] = Field(default_factory=list)
    hypothetical: bool = False
    description: str = ""


class SceneRevOut(RevisionOut):
    scene_id: str
    parent_revision_id: str | None


def _scene(db, user, scene_id: str, action=Action.view) -> Scene:
    sc = db.get(Scene, scene_id)
    if sc is None:
        raise ApiError(404, "not_found", "Scene not found")
    require_project(db, user, sc.project_id, action)
    return sc


def _rev(db, user, rev_id: str, action=Action.view) -> tuple[SceneRevision, Scene]:
    rev = db.get(SceneRevision, rev_id)
    if rev is None:
        raise ApiError(404, "not_found", "Scene revision not found")
    return rev, _scene(db, user, rev.scene_id, action)


def geometry_warnings(content: SceneRevisionContent) -> list[dict]:
    out = []
    for o in content.spatial_objects:
        if o.geometry.type in ("Polygon", "MultiPolygon"):
            out += [i.model_dump() for i in validate_polygon(o.geometry, f"spatial_objects[{o.id}]", f"{o.kind} {o.name}")]
    return out


@router.get("/projects/{project_id}/scenes")
def list_scenes(project_id: str, user: CurrentUser, db: DB):
    require_project(db, user, project_id)
    return [{"id": s.id, "name": s.name, "current_revision_id": s.current_revision_id, "created_at": s.created_at}
            for s in db.scalars(select(Scene).where(Scene.project_id == project_id).order_by(Scene.created_at))]


@router.post("/projects/{project_id}/scenes", response_model=SceneRevOut, status_code=201)
def create_scene(project_id: str, body: SceneIn, user: CurrentUser, db: DB):
    """Create a scene from imported visual assets (Gaussian files, point clouds, meshes, terrain)."""
    require_project(db, user, project_id, Action.edit)
    visuals = []
    for aid in body.visual_asset_ids:
        a = db.get(SourceAsset, aid)
        if a is None or a.project_id != project_id:
            raise ApiError(404, "not_found", f"Asset {aid} not found")
        if a.kind not in VISUAL_KIND:
            raise ApiError(422, "not_visual", f"Asset {a.filename} ({a.kind}) is not a visual asset")
        visuals.append(VisualAsset(id=f"v_{aid[:8]}", asset_id=aid, kind=VISUAL_KIND[a.kind],
                                   format=a.filename.rsplit(".", 1)[-1].lower(), label=a.filename, frame="capture",
                                   provenance=Provenance(kind=ProvenanceKind.imported, source=a.filename, source_ref=aid)))
    sc = Scene(project_id=project_id, name=body.name)
    db.add(sc)
    db.flush()
    quality = QualityAssessment()
    if visuals:
        quality.mark(QualityState.visual_reconstruction_available, "Imported visual asset(s)", user.id, utcnow())
    content = SceneRevisionContent(
        scene_id=sc.id, hypothetical=body.hypothetical, visual_assets=visuals, quality=quality,
        source_asset_ids=body.visual_asset_ids,
        coordinates=SceneCoordinates(frames=[CoordinateFrame(id="canonical", kind=FrameKind.canonical),
                                             CoordinateFrame(id="capture", kind=FrameKind.capture)],
                                     transforms=[Transform(from_frame="capture", to_frame="canonical",
                                                           method="identity (scale not established)")]),
        provenance=Provenance(kind=ProvenanceKind.imported, recorded_by=user.id, recorded_at=utcnow()),
    )
    rev = create_scene_revision(db, sc, content, user.id, message=body.description or "created")
    audit(db, action="scene.created", object_type="scene", object_id=sc.id, actor_id=user.id, project_id=project_id)
    return rev


def quality_view(db, user, rev: SceneRevision, sc: Scene, content: SceneRevisionContent) -> list[dict]:
    """Quality states as recorded on the revision, completed by the downstream states the records now show.

    A revision is immutable, so model preparation, review and integration checks that happen later are
    read from the models, reviews and runs that cite it. Calibration is never inferred.
    """
    cases = set(visible_case_ids(db, user, sc.project_id))
    in_view = lambda case_id: case_id is None or case_id in cases  # noqa: E731
    models = [
        (mr, dm)
        for mr, dm in db.execute(
            select(DomainModelRevision, DomainModel)
            .join(DomainModel, DomainModel.id == DomainModelRevision.domain_model_id)
            .where(DomainModel.project_id == sc.project_id, DomainModelRevision.content["scene_revision_id"].astext == rev.id)
            .order_by(DomainModelRevision.created_at)
        )
        if in_view(dm.case_id)
    ]
    ids = [mr.id for mr, _ in models]
    name = {mr.id: f"{dm.name} revision {mr.number}" for mr, dm in models}
    approved = db.scalars(
        select(ReviewRecord).where(
            ReviewRecord.object_type == "domain_model_revision", ReviewRecord.object_id.in_(ids), ReviewRecord.status == "approved"
        )
    ).all() if ids else []
    runs = [
        r
        for r in db.scalars(
            select(SimulationRun).where(SimulationRun.domain_model_revision_id.in_(ids), SimulationRun.state == "completed")
        )
        if in_view(r.case_id)
    ] if ids else []
    verified = [r for r in runs if (cc := r.result_summary.get("crosschecks") or []) and all(c.get("passed") for c in cc)]

    derived: dict[QualityState, tuple[bool, str]] = {
        QualityState.domain_model_prepared: (
            bool(models),
            "; ".join(name[i] for i in ids) if models else "No domain model cites this scene revision.",
        ),
        QualityState.model_reviewed: (
            bool(approved),
            "; ".join(f"Approved review of {name[r.object_id]}" for r in approved) if approved
            else "No approved review of a model prepared from this revision.",
        ),
        QualityState.simulation_integration_verified: (
            bool(verified),
            f"{len(verified)} completed run(s) passed all their native cross-checks, "
            f"for example {verified[0].label or verified[0].id[:8]}"
            if verified else ("Completed runs have no fully passing native cross-checks." if runs else "No completed run uses a model from this revision."),
        ),
    }
    out = []
    for rec in content.quality.records:
        if not rec.achieved and rec.state in derived:
            ok, evidence = derived[rec.state]
            out.append({"state": rec.state.value, "label": QUALITY_LABELS[rec.state], "achieved": ok, "evidence": evidence, "source": "derived"})
        else:
            evidence = rec.evidence or ("Requires calibration against independent observations; none recorded."
                                        if rec.state == QualityState.model_calibrated else None)
            out.append({"state": rec.state.value, "label": QUALITY_LABELS[rec.state], "achieved": rec.achieved,
                        "evidence": evidence, "source": "recorded"})
    return out


@router.get("/scenes/{scene_id}/revisions", response_model=list[SceneRevOut])
def scene_revisions(scene_id: str, user: CurrentUser, db: DB):
    _scene(db, user, scene_id)
    return db.scalars(select(SceneRevision).where(SceneRevision.scene_id == scene_id).order_by(SceneRevision.number.desc())).all()


@router.get("/scene-revisions/{rev_id}")
def get_scene_revision(rev_id: str, user: CurrentUser, db: DB):
    rev, sc = _rev(db, user, rev_id)
    content = SceneRevisionContent.model_validate(rev.content)
    assets = {a.id: {"id": a.id, "filename": a.filename, "kind": a.kind, "size_bytes": a.size_bytes, "sha256": a.sha256}
              for a in db.scalars(select(SourceAsset).where(SourceAsset.id.in_([v.asset_id for v in content.visual_assets])))}
    graph = FrameGraph(content.coordinates.transforms)
    frames = {}
    for v in content.visual_assets:
        try:
            frames[v.id] = to_list(graph.resolve(v.frame, "canonical"))
        except KeyError:
            frames[v.id] = None
    return {**SceneRevOut.model_validate(rev).model_dump(mode="json"), "scene_name": sc.name, "project_id": sc.project_id,
            "visual_asset_files": assets, "visual_to_canonical": frames, "geometry_warnings": geometry_warnings(content),
            "quality_view": quality_view(db, user, rev, sc, content)}


@router.post("/scenes/{scene_id}/revisions", response_model=SceneRevOut, status_code=201)
def save_scene_revision(scene_id: str, body: RevisionIn, user: CurrentUser, db: DB):
    sc = _scene(db, user, scene_id, Action.edit)
    content = SceneRevisionContent.model_validate(body.content)
    current = db.get(SceneRevision, sc.current_revision_id) if sc.current_revision_id else None
    if body.base_revision_id and current and body.base_revision_id != current.id:
        raise ApiError(409, "stale_revision", "The scene changed since you loaded it", {"current_revision_id": current.id})
    tol = content.tolerance
    content = content.model_copy(update={"measurements": [evaluate(m, tol) for m in content.measurements]})
    if current is not None and current.status == "draft":
        rev = update_scene_draft(db, current, content)
        rev.message = body.message or rev.message
    else:
        rev = create_scene_revision(db, sc, content, user.id, message=body.message, parent_id=current.id if current else None)
    audit(db, action="scene.revised", object_type="scene_revision", object_id=rev.id, actor_id=user.id,
          project_id=sc.project_id, details={"number": rev.number})
    return rev


@router.post("/scene-revisions/{rev_id}/publish", response_model=SceneRevOut)
def publish_scene_revision(rev_id: str, user: CurrentUser, db: DB):
    rev, sc = _rev(db, user, rev_id, Action.edit)
    publish(db, rev)
    audit(db, action="scene.published", object_type="scene_revision", object_id=rev.id, actor_id=user.id,
          project_id=sc.project_id)
    return rev


class MeasureIn(BaseModel):
    measurement: Measurement


@router.post("/scene-revisions/{rev_id}/measurements/evaluate")
def evaluate_measurement(rev_id: str, body: MeasureIn, user: CurrentUser, db: DB):
    rev, _ = _rev(db, user, rev_id)
    content = SceneRevisionContent.model_validate(rev.content)
    return evaluate(body.measurement, content.tolerance).model_dump(mode="json")


class AlignmentPair(BaseModel):
    label: str
    reconstructed: list[float] = Field(min_length=3, max_length=3, description="Picked point in the capture frame")
    reference: list[float] = Field(min_length=3, max_length=3, description="Surveyed coordinates (canonical frame)")


class AlignmentIn(BaseModel):
    method: Literal["control_points", "two_point_scale"]
    pairs: list[AlignmentPair] = Field(default_factory=list)
    reference_distance_m: float | None = None
    provenance: Provenance
    message: str = ""


@router.post("/scenes/{scene_id}/alignment", response_model=SceneRevOut, status_code=201)
def align(scene_id: str, body: AlignmentIn, user: CurrentUser, db: DB):
    """Establish scale/orientation: similarity from >=3 control points, or uniform scale from a known distance."""
    sc = _scene(db, user, scene_id, Action.edit)
    cur = db.get(SceneRevision, sc.current_revision_id)
    content = SceneRevisionContent.model_validate(cur.content)  # type: ignore[union-attr]
    if body.method == "control_points":
        if len(body.pairs) < 3:
            raise ApiError(422, "too_few_points", "At least three control points are required")
        try:
            m, rms = umeyama(np.array([p.reconstructed for p in body.pairs]), np.array([p.reference for p in body.pairs]))
        except ValueError as exc:
            raise ApiError(422, "degenerate_points", str(exc)) from exc
        method = f"similarity from {len(body.pairs)} control points (Umeyama), RMS residual {rms:.4f} m"
        evidence = method
        scale_ok = True
    else:
        if len(body.pairs) != 2 or not body.reference_distance_m:
            raise ApiError(422, "two_points_required", "Two picked points and a reference distance are required")
        s = scale_from_reference(body.pairs[0].reconstructed, body.pairs[1].reconstructed, body.reference_distance_m)
        m, rms = similarity(s), None
        method = f"uniform scale {s:.6f} from a {body.reference_distance_m} m reference distance (orientation unchanged)"
        evidence = method
        scale_ok = True
    transforms = [t for t in content.coordinates.transforms if not (t.from_frame == "capture" and t.to_frame == "canonical")]
    transforms.append(Transform(from_frame="capture", to_frame="canonical", matrix=to_list(m), method=method,
                                residual_rms_m=rms, provenance=body.provenance))
    coords = content.coordinates.model_copy(update={"transforms": transforms})
    quality = content.quality.model_copy(deep=True)
    if scale_ok:
        quality.mark(QualityState.scale_established, evidence, user.id, utcnow())
    # measurements were picked in the old canonical frame: re-express them via the capture frame
    old = FrameGraph(content.coordinates.transforms)
    try:
        to_capture = np.linalg.inv(old.resolve("capture", "canonical"))
    except KeyError:
        to_capture = np.eye(4)
    remapped = []
    for meas in content.measurements:
        a = apply(m @ to_capture, meas.point_a).tolist()
        b = apply(m @ to_capture, meas.point_b).tolist()
        remapped.append(evaluate(meas.model_copy(update={"point_a": a, "point_b": b}), content.tolerance))
    new = content.model_copy(update={"coordinates": coords, "quality": quality, "measurements": remapped})
    rev = create_scene_revision(db, sc, new, user.id, message=body.message or method, parent_id=cur.id)  # type: ignore[union-attr]
    audit(db, action="scene.aligned", object_type="scene_revision", object_id=rev.id, actor_id=user.id,
          project_id=sc.project_id, details={"method": body.method, "rms_m": rms})
    return rev


@router.get("/scene-revisions/{rev_id}/objects")
def objects_in_bbox(rev_id: str, user: CurrentUser, db: DB, xmin: float, ymin: float, xmax: float, ymax: float):
    """Spatial query (PostGIS) over the materialised objects of a scene revision."""
    _rev(db, user, rev_id)
    env = func.ST_MakeEnvelope(xmin, ymin, xmax, ymax, 0)
    rows = db.scalars(select(SpatialObject).where(SpatialObject.scene_revision_id == rev_id, SpatialObject.geom.is_not(None),
                                                  func.ST_Intersects(SpatialObject.geom, env))).all()
    return [{"id": o.key, "name": o.name, "kind": o.kind, "layer": o.layer, "belongs_to": o.belongs_to} for o in rows]


class DeriveIn(BaseModel):
    discipline: Literal["pedestrian"] = "pedestrian"
    name: str
    population_default: int = 20


@router.post("/scene-revisions/{rev_id}/derive-model", status_code=201)
def derive_model(rev_id: str, body: DeriveIn, user: CurrentUser, db: DB):
    """Prepare a pedestrian domain model from prepared-model objects in a scene revision.

    Uses objects of kind walkable_area, obstacle, exit/refuge, start_zone and route_segment. Captured appearance
    is never treated as walkable geometry automatically: only explicitly prepared objects are used.
    """
    rev, sc = _rev(db, user, rev_id, Action.edit)
    content = SceneRevisionContent.model_validate(rev.content)
    objs = content.spatial_objects

    def polys(kind: str) -> list:
        return [o for o in objs if o.kind == kind and o.geometry.type == "Polygon"]

    walk = polys("walkable_area")
    if not walk:
        raise ApiError(422, "no_walkable_area", "The scene has no prepared walkable_area polygons")
    if len(walk) == 1:
        area = PolygonGeom.model_validate(walk[0].geometry.model_dump())
    else:
        area = MultiPolygonGeom(coordinates=[w.geometry.coordinates for w in walk])  # type: ignore[union-attr]
    exits = [ExitDef(id=o.id, name=o.name, kind="refuge" if o.kind == "refuge" else "exit",
                     polygon=PolygonGeom.model_validate(o.geometry.model_dump()), provenance=o.provenance)
             for o in objs if o.kind in ("exit", "refuge") and o.geometry.type == "Polygon"]
    if not exits:
        raise ApiError(422, "no_exits", "The scene has no prepared exit or refuge polygons")
    groups = []
    for o in polys("start_zone"):
        n = o.properties.get("population")
        count = int(n.value) if n is not None else body.population_default
        groups.append(StartGroup(id=o.id, name=o.name, zone=PolygonGeom.model_validate(o.geometry.model_dump()), count=count,
                                 provenance=n.provenance if n is not None else Provenance(
                                     kind=ProvenanceKind.assumption, note=f"default population {body.population_default}")))
    model = PedestrianModel(
        walkable_area=area,
        obstacles=[NamedPolygon(id=o.id, name=o.name, polygon=PolygonGeom.model_validate(o.geometry.model_dump()),
                                provenance=o.provenance) for o in polys("obstacle")],
        exits=exits, start_groups=groups,
        route_segments=[RouteSegment(id=o.id, name=o.name, polygon=PolygonGeom.model_validate(o.geometry.model_dump()))
                        for o in polys("route_segment")],
    )
    env = DomainModelEnvelope(discipline="pedestrian", name=body.name, scene_revision_id=rev.id, content=model,
                              provenance=Provenance(kind=ProvenanceKind.derived, derived_from=[rev.id],
                                                    note="Derived from prepared scene objects"))
    from ..services.templates import add_model

    project = require_project(db, user, sc.project_id, Action.edit)
    dm = add_model(db, project, env, user.id, name=body.name)
    audit(db, action="model.derived_from_scene", object_type="domain_model", object_id=dm.id, actor_id=user.id,
          project_id=sc.project_id, details={"scene_revision_id": rev.id})
    return {"domain_model_id": dm.id, "revision_id": dm.current_revision_id}


def measurement_from_points(label: str, a: list[float], b: list[float], ref: float | None, prov: Provenance) -> Measurement:
    return Measurement(id=f"m_{label}", label=label, purpose="independent_check", point_a=a, point_b=b,
                       reference_distance=SourcedQuantity(value=ref, unit="m", provenance=prov) if ref else None,
                       source="picked points")


__all__ = ["DomainModel", "router"]
