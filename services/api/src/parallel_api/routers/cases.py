"""CrashLens case workflows (/api/v1/cases) and evidence records (/api/v1/evidence).

Scene reconstruction (what was recorded) stays separate from event
reconstruction (how the incident may have unfolded). Every endpoint checks
explicit case assignment; organisation or project access alone never exposes a
case. Originals are preserved: derivatives are new files linked to the source
hash, and handling events record receipt, access, verification and
derivation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select

from parallel_contracts.crashlens import PLAYBACK_LABELS, HypothesisContent, PlaybackSource
from parallel_contracts.geometry import Geometry
from parallel_contracts.provenance import Provenance, Uncertainty
from parallel_contracts.units import UnitError, unit_def
from parallel_core.access import Action, case_role, org_role, project_role, require_case, require_project
from parallel_core.audit import record as audit
from parallel_core.models import (
    AuditEvent,
    Case,
    CaseAssignment,
    EvidenceDerivative,
    EvidenceHandlingEvent,
    EvidenceItem,
    EvidenceObservation,
    ExternalAnalysisRecord,
    Hypothesis,
    HypothesisEvidenceLink,
    Project,
    ReviewRecord,
    RunArtifact,
    SimulationRun,
    SourceAsset,
    TimelineEvent,
    User,
    VehicleRecord,
    WitnessStatementReference,
)
from parallel_core.settings import get_settings
from parallel_core.storage import get_storage
from parallel_metrics.collision import skid_speed_metrics

from ..deps import DB, CurrentUser
from ..errors import ApiError
from ..schemas import (
    SPATIAL_OBSERVATION_KINDS,
    AnalysisIn,
    AssignmentIn,
    AssignmentOut,
    CaseIn,
    CaseOut,
    CasePatch,
    CollisionModelIn,
    DerivativeOut,
    EvidenceOut,
    HandlingEventOut,
    HypothesisIn,
    HypothesisOut,
    HypothesisPatch,
    LinkIn,
    LinkOut,
    ObservationIn,
    ObservationOut,
    SignedUrlOut,
    SkidSpeedIn,
    TimelineIn,
    TimelineOut,
    VehicleIn,
    VehicleOut,
    WitnessIn,
    WitnessOut,
)
from .assets import received_file, store_upload

router = APIRouter(tags=["cases"])

EVIDENCE_KINDS = {"image", "video", "document", "vehicle_data", "measurement", "other"}
FILE_KIND_RULES = {"image": {"image"}, "video": {"video"}, "document": {"document"}}
DERIVATIVE_KINDS = {"redaction", "enhancement", "annotation_overlay", "crop", "frame_extract"}
_GEOM = TypeAdapter(Geometry)


# ---------------------------------------------------------------- helpers
def _audit(db, case: Case, actor: str | None, action: str, object_type: str, object_id: str, /, **details):
    audit(
        db,
        action=action,
        object_type=object_type,
        object_id=object_id,
        actor_id=actor,
        project_id=case.project_id,
        details={"case_id": case.id, **details},
    )


def _handling(db, item: EvidenceItem, case: Case, actor: str | None, action: str, /, **details) -> None:
    db.add(EvidenceHandlingEvent(evidence_item_id=item.id, action=action, actor_id=actor, details=details))
    _audit(db, case, actor, f"evidence.{action}", "evidence_item", item.id, **details)


def _case_out(db, user, case: Case) -> CaseOut:
    out = CaseOut.model_validate(case)
    out.my_role = case_role(db, user, case)
    return out


def _evidence(db, user, evidence_id: str, action: Action = Action.view) -> tuple[EvidenceItem, Case]:
    item = db.get(EvidenceItem, evidence_id)
    if item is None:
        raise ApiError(404, "not_found", "Evidence item not found")
    return item, require_case(db, user, item.case_id, action)


def _hypothesis(db, user, hid: str, action: Action = Action.view) -> tuple[Hypothesis, Case]:
    h = db.get(Hypothesis, hid)
    if h is None:
        raise ApiError(404, "not_found", "Hypothesis not found")
    return h, require_case(db, user, h.case_id, action)


def _in_case(db, model, oid: str | None, case_id: str, label: str):
    if oid is None:
        return None
    obj = db.get(model, oid)
    if obj is None or obj.case_id != case_id:
        raise ApiError(422, "invalid_reference", f"{label} {oid} is not part of this case")
    return obj


def _hyp_out(db, h: Hypothesis) -> HypothesisOut:
    out = HypothesisOut.model_validate(h)
    out.links = [
        LinkOut.model_validate(li)
        for li in db.scalars(
            select(HypothesisEvidenceLink)
            .where(HypothesisEvidenceLink.hypothesis_id == h.id)
            .order_by(HypothesisEvidenceLink.created_at)
        )
    ]
    return out


def _validate_content(content: dict) -> dict:
    try:
        return HypothesisContent.model_validate(content).model_dump(mode="json")
    except ValidationError as e:
        raise ApiError(
            422, "invalid_hypothesis", "Hypothesis content is invalid", {"errors": e.errors()}
        ) from e


# ---------------------------------------------------------------- cases and assignments
@router.post("/projects/{project_id}/cases", response_model=CaseOut, status_code=201)
def create_case(project_id: str, body: CaseIn, user: CurrentUser, db: DB):
    project = require_project(db, user, project_id, Action.edit)
    if project.project_type != "crashlens":
        raise ApiError(422, "not_crashlens", "Cases belong to CrashLens projects")
    if db.scalar(select(Case.id).where(Case.project_id == project_id, Case.reference == body.reference)):
        raise ApiError(409, "duplicate_reference", f"Case reference {body.reference} already exists")
    case = Case(project_id=project_id, created_by=user.id, **body.model_dump())
    db.add(case)
    db.flush()
    db.add(CaseAssignment(case_id=case.id, user_id=user.id, role="investigator", assigned_by=user.id))
    _audit(db, case, user.id, "case.created", "case", case.id, reference=case.reference)
    db.flush()
    return _case_out(db, user, case)


@router.get("/projects/{project_id}/cases", response_model=list[CaseOut])
def list_cases(project_id: str, user: CurrentUser, db: DB):
    require_project(db, user, project_id)
    rows = db.scalars(
        select(Case)
        .join(CaseAssignment, CaseAssignment.case_id == Case.id)
        .where(Case.project_id == project_id, CaseAssignment.user_id == user.id)
        .order_by(Case.created_at.desc())
    ).all()
    return [_case_out(db, user, c) for c in rows]


@router.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: str, user: CurrentUser, db: DB):
    return _case_out(db, user, require_case(db, user, case_id))


@router.patch("/cases/{case_id}", response_model=CaseOut)
def patch_case(case_id: str, body: CasePatch, user: CurrentUser, db: DB):
    case = require_case(db, user, case_id, Action.edit)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(case, k, v)
    _audit(db, case, user.id, "case.updated", "case", case.id, fields=sorted(changes))
    return _case_out(db, user, case)


@router.get("/cases/{case_id}/assignments", response_model=list[AssignmentOut])
def list_assignments(case_id: str, user: CurrentUser, db: DB):
    require_case(db, user, case_id)
    return db.scalars(select(CaseAssignment).where(CaseAssignment.case_id == case_id)).all()


def _may_assign(db, user, case: Case) -> None:
    """Assigned investigators and organisation administrators manage assignments (every change is audited)."""
    project = db.get(Project, case.project_id)
    if case_role(db, user, case) == "investigator":
        return
    if project is not None and org_role(db, user, project.organisation_id) == "admin":
        return
    raise ApiError(404, "not_found", "Case not found")


@router.post("/cases/{case_id}/assignments", response_model=AssignmentOut, status_code=201)
def assign(case_id: str, body: AssignmentIn, user: CurrentUser, db: DB):
    case = db.get(Case, case_id)
    if case is None:
        raise ApiError(404, "not_found", "Case not found")
    _may_assign(db, user, case)
    target = db.get(User, body.user_id)
    project = db.get(Project, case.project_id)
    if target is None or project is None or project_role(db, target, project) is None:
        raise ApiError(422, "not_project_member", "Assign only people who are members of the project")
    existing = db.scalar(
        select(CaseAssignment).where(
            CaseAssignment.case_id == case_id, CaseAssignment.user_id == body.user_id
        )
    )
    if existing is not None:
        existing.role = body.role
        a = existing
    else:
        a = CaseAssignment(case_id=case_id, user_id=body.user_id, role=body.role, assigned_by=user.id)
        db.add(a)
    db.flush()
    _audit(db, case, user.id, "case.assigned", "case", case.id, user_id=body.user_id, role=body.role)
    return a


@router.delete("/cases/{case_id}/assignments/{user_id}", status_code=204)
def unassign(case_id: str, user_id: str, user: CurrentUser, db: DB):
    case = db.get(Case, case_id)
    if case is None:
        raise ApiError(404, "not_found", "Case not found")
    _may_assign(db, user, case)
    a = db.scalar(
        select(CaseAssignment).where(CaseAssignment.case_id == case_id, CaseAssignment.user_id == user_id)
    )
    if a is None:
        raise ApiError(404, "not_found", "Assignment not found")
    investigators = db.scalar(
        select(func.count()).where(CaseAssignment.case_id == case_id, CaseAssignment.role == "investigator")
    )
    if a.role == "investigator" and investigators == 1:
        raise ApiError(409, "last_investigator", "A case keeps at least one investigator")
    db.delete(a)
    _audit(db, case, user.id, "case.unassigned", "case", case.id, user_id=user_id)


@router.get("/cases/{case_id}/activity")
def case_activity(case_id: str, user: CurrentUser, db: DB):
    require_case(db, user, case_id)
    rows = db.execute(
        select(AuditEvent, User.display_name)
        .outerjoin(User, User.id == AuditEvent.actor_id)
        .where(AuditEvent.details["case_id"].astext == case_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(500)
    ).all()
    return [
        {
            "id": e.id,
            "action": e.action,
            "object_type": e.object_type,
            "object_id": e.object_id,
            "actor": name,
            "details": e.details,
            "created_at": e.created_at,
        }
        for e, name in rows
    ]


# ---------------------------------------------------------------- evidence
@router.post("/cases/{case_id}/evidence", response_model=EvidenceOut, status_code=201, tags=["evidence"])
def add_evidence(
    case_id: str,
    user: CurrentUser,
    db: DB,
    kind: str = Form(...),
    title: str = Form(..., min_length=2, max_length=300),
    description: str = Form(""),
    claimed_captured_at: datetime | None = Form(None),
    captured_by: str | None = Form(None),
    source_metadata: str = Form("{}", description="JSON object: device, chain of custody notes, ..."),
    file: UploadFile | None = File(None),
):
    case = require_case(db, user, case_id, Action.edit)
    if kind not in EVIDENCE_KINDS:
        raise ApiError(422, "invalid_kind", f"Evidence kind must be one of {sorted(EVIDENCE_KINDS)}")
    try:
        meta = json.loads(source_metadata)
        assert isinstance(meta, dict)
    except (ValueError, AssertionError) as e:
        raise ApiError(422, "invalid_metadata", "source_metadata must be a JSON object") from e
    asset = None
    if file is not None:
        with received_file(file) as tmp:
            asset = store_upload(
                db,
                project_id=case.project_id,
                user_id=user.id,
                file_path=tmp,
                filename=file.filename or "evidence",
                batch_id=None,
                sensitivity="evidence",
            )
        if kind in FILE_KIND_RULES and asset.kind not in FILE_KIND_RULES[kind]:
            raise ApiError(422, "kind_mismatch", f"The file is {asset.kind}, not {kind}")
    elif kind in FILE_KIND_RULES:
        raise ApiError(422, "file_required", f"{kind} evidence needs its original file")
    item = EvidenceItem(
        case_id=case.id,
        source_asset_id=asset.id if asset else None,
        kind=kind,
        title=title,
        description=description,
        claimed_captured_at=claimed_captured_at,
        captured_by=captured_by,
        source_metadata={**meta, **({"inspection": asset.inspection} if asset else {})},
        original_sha256=asset.sha256 if asset else None,
        received_by=user.id,
    )
    db.add(item)
    db.flush()
    _handling(
        db,
        item,
        case,
        user.id,
        "received",
        filename=asset.filename if asset else None,
        sha256=item.original_sha256,
        size_bytes=asset.size_bytes if asset else None,
    )
    return item


@router.get("/cases/{case_id}/evidence", response_model=list[EvidenceOut], tags=["evidence"])
def list_evidence(case_id: str, user: CurrentUser, db: DB):
    require_case(db, user, case_id)
    return db.scalars(
        select(EvidenceItem).where(EvidenceItem.case_id == case_id).order_by(EvidenceItem.created_at)
    ).all()


@router.get("/evidence/{evidence_id}", response_model=EvidenceOut, tags=["evidence"])
def get_evidence(evidence_id: str, user: CurrentUser, db: DB):
    return _evidence(db, user, evidence_id)[0]


@router.get("/evidence/{evidence_id}/handling", response_model=list[HandlingEventOut], tags=["evidence"])
def handling(evidence_id: str, user: CurrentUser, db: DB):
    item, _ = _evidence(db, user, evidence_id)
    return db.scalars(
        select(EvidenceHandlingEvent)
        .where(EvidenceHandlingEvent.evidence_item_id == item.id)
        .order_by(EvidenceHandlingEvent.created_at)
    ).all()


@router.get("/evidence/{evidence_id}/original-url", response_model=SignedUrlOut, tags=["evidence"])
def original_url(evidence_id: str, user: CurrentUser, db: DB):
    item, case = _evidence(db, user, evidence_id)
    asset = db.get(SourceAsset, item.source_asset_id) if item.source_asset_id else None
    if asset is None:
        raise ApiError(404, "not_found", "This evidence item has no original file")
    _handling(db, item, case, user.id, "accessed", purpose="original download URL issued")
    s = get_settings()
    return SignedUrlOut(
        url=get_storage().signed_url(asset.storage_key, asset.filename), expires_in_s=s.signed_url_seconds
    )


@router.post("/evidence/{evidence_id}/verify", tags=["evidence"])
def verify(evidence_id: str, user: CurrentUser, db: DB):
    """Re-hash the stored original and compare with the hash recorded at receipt."""
    item, case = _evidence(db, user, evidence_id)
    asset = db.get(SourceAsset, item.source_asset_id) if item.source_asset_id else None
    if asset is None:
        raise ApiError(404, "not_found", "This evidence item has no original file")
    h = hashlib.sha256()
    store = get_storage()
    if hasattr(store, "iter_file"):
        for chunk in store.iter_file(asset.storage_key):
            h.update(chunk)
    else:
        h.update(store.get_bytes(asset.storage_key))
    current = h.hexdigest()
    ok = current == item.original_sha256
    _handling(db, item, case, user.id, "integrity_verified", matches=ok, sha256=current)
    return {
        "evidence_item_id": item.id,
        "recorded_sha256": item.original_sha256,
        "current_sha256": current,
        "matches": ok,
        "note": EvidenceOut.model_fields["integrity_note"].default,
    }


@router.post(
    "/evidence/{evidence_id}/derivatives", response_model=DerivativeOut, status_code=201, tags=["evidence"]
)
def add_derivative(
    evidence_id: str,
    user: CurrentUser,
    db: DB,
    kind: str = Form(...),
    method: str = Form(..., min_length=3, description="What was done, with which tool and settings"),
    file: UploadFile = File(...),
):
    item, case = _evidence(db, user, evidence_id, Action.edit)
    if kind not in DERIVATIVE_KINDS:
        raise ApiError(422, "invalid_kind", f"Derivative kind must be one of {sorted(DERIVATIVE_KINDS)}")
    if not item.original_sha256 or not item.source_asset_id:
        raise ApiError(422, "no_original", "Derivatives need an original file")
    with received_file(file) as tmp:
        asset = store_upload(
            db,
            project_id=case.project_id,
            user_id=user.id,
            file_path=tmp,
            filename=file.filename or "derivative",
            batch_id=None,
            sensitivity="evidence",
            derived_from_id=item.source_asset_id,
        )
    if asset.sha256 == item.original_sha256:
        raise ApiError(422, "identical_to_original", "The derivative is byte-identical to the original")
    d = EvidenceDerivative(
        evidence_item_id=item.id,
        kind=kind,
        source_sha256=item.original_sha256,
        derived_asset_id=asset.id,
        method=method,
        created_by=user.id,
    )
    db.add(d)
    db.flush()
    _handling(
        db, item, case, user.id, "derivative_created", derivative_id=d.id, kind=kind, sha256=asset.sha256
    )
    return d


@router.get("/evidence/{evidence_id}/derivatives", response_model=list[DerivativeOut], tags=["evidence"])
def list_derivatives(evidence_id: str, user: CurrentUser, db: DB):
    item, _ = _evidence(db, user, evidence_id)
    return db.scalars(select(EvidenceDerivative).where(EvidenceDerivative.evidence_item_id == item.id)).all()


@router.get("/evidence-derivatives/{derivative_id}/url", response_model=SignedUrlOut, tags=["evidence"])
def derivative_url(derivative_id: str, user: CurrentUser, db: DB):
    d = db.get(EvidenceDerivative, derivative_id)
    if d is None:
        raise ApiError(404, "not_found", "Derivative not found")
    item, case = _evidence(db, user, d.evidence_item_id)
    asset = db.get(SourceAsset, d.derived_asset_id)
    _handling(
        db, item, case, user.id, "accessed", purpose="derivative download URL issued", derivative_id=d.id
    )
    return SignedUrlOut(
        url=get_storage().signed_url(asset.storage_key, asset.filename),  # type: ignore[union-attr]
        expires_in_s=get_settings().signed_url_seconds,
    )


# ---------------------------------------------------------------- observations and interpretations
@router.post("/cases/{case_id}/observations", response_model=ObservationOut, status_code=201)
def add_observation(case_id: str, body: ObservationIn, user: CurrentUser, db: DB):
    case = require_case(db, user, case_id, Action.edit)
    _in_case(db, EvidenceItem, body.evidence_item_id, case_id, "Evidence item")
    if body.statement_type == "observation":
        if not body.evidence_item_id:
            raise ApiError(
                422, "evidence_source_required", "An observation records the evidence it comes from"
            )
        if body.based_on:
            raise ApiError(
                422,
                "observation_not_derived",
                "Observations record what evidence shows; reasoning "
                "from other observations is an interpretation",
            )
        if body.kind in SPATIAL_OBSERVATION_KINDS and body.position is None and body.geometry is None:
            raise ApiError(422, "spatial_reference_required", f"A {body.kind} observation needs a location")
    else:
        if not body.based_on:
            raise ApiError(422, "basis_required", "An interpretation lists the observations it reasons from")
        for oid in body.based_on:
            o = _in_case(db, EvidenceObservation, oid, case_id, "Observation")
            if o.statement_type != "observation":
                raise ApiError(422, "invalid_basis", f"{oid} is not an observation")
    if body.geometry is not None:
        try:
            _GEOM.validate_python(body.geometry)
        except ValidationError as e:
            raise ApiError(422, "invalid_geometry", "Geometry is invalid", {"errors": e.errors()}) from e
    if (body.measurement_value is None) != (body.measurement_unit is None):
        raise ApiError(422, "measurement_unit", "A measurement needs both a value and a unit")
    if body.measurement_unit:
        try:
            unit_def(body.measurement_unit)
        except UnitError as e:
            raise ApiError(422, "invalid_unit", str(e)) from e
    if body.uncertainty is not None:
        try:
            Uncertainty.model_validate(body.uncertainty)
        except ValidationError as e:
            raise ApiError(
                422, "invalid_uncertainty", "Uncertainty is invalid", {"errors": e.errors()}
            ) from e
    o = EvidenceObservation(case_id=case_id, author_id=user.id, **body.model_dump())
    db.add(o)
    db.flush()
    _audit(db, case, user.id, f"{body.statement_type}.recorded", "evidence_observation", o.id, kind=body.kind)
    return o


@router.get("/cases/{case_id}/observations", response_model=list[ObservationOut])
def list_observations(case_id: str, user: CurrentUser, db: DB, statement_type: str | None = None):
    require_case(db, user, case_id)
    q = select(EvidenceObservation).where(EvidenceObservation.case_id == case_id)
    if statement_type:
        q = q.where(EvidenceObservation.statement_type == statement_type)
    return db.scalars(q.order_by(EvidenceObservation.created_at)).all()


# ---------------------------------------------------------------- vehicles, witnesses, timeline
@router.post("/cases/{case_id}/vehicles", response_model=VehicleOut, status_code=201)
def add_vehicle(case_id: str, body: VehicleIn, user: CurrentUser, db: DB):
    case = require_case(db, user, case_id, Action.edit)
    try:
        prov = Provenance.model_validate(body.provenance).model_dump(mode="json")
    except ValidationError as e:
        raise ApiError(422, "invalid_provenance", "Provenance is invalid", {"errors": e.errors()}) from e
    v = VehicleRecord(case_id=case_id, **{**body.model_dump(), "provenance": prov})
    db.add(v)
    db.flush()
    _audit(db, case, user.id, "vehicle.recorded", "vehicle_record", v.id, label=v.label)
    return v


@router.get("/cases/{case_id}/vehicles", response_model=list[VehicleOut])
def list_vehicles(case_id: str, user: CurrentUser, db: DB):
    require_case(db, user, case_id)
    return db.scalars(
        select(VehicleRecord).where(VehicleRecord.case_id == case_id).order_by(VehicleRecord.created_at)
    ).all()


@router.post("/cases/{case_id}/witness-references", response_model=WitnessOut, status_code=201)
def add_witness(case_id: str, body: WitnessIn, user: CurrentUser, db: DB):
    case = require_case(db, user, case_id, Action.edit)
    item = _in_case(db, EvidenceItem, body.evidence_item_id, case_id, "Evidence item")
    w = WitnessStatementReference(
        case_id=case_id,
        reference=body.reference,
        summary=body.summary,
        obtained_at=body.obtained_at,
        source_asset_id=item.source_asset_id if item else None,
    )
    db.add(w)
    db.flush()
    _audit(db, case, user.id, "witness_reference.recorded", "witness_statement_reference", w.id)
    return w


@router.get("/cases/{case_id}/witness-references", response_model=list[WitnessOut])
def list_witnesses(case_id: str, user: CurrentUser, db: DB):
    require_case(db, user, case_id)
    return db.scalars(
        select(WitnessStatementReference).where(WitnessStatementReference.case_id == case_id)
    ).all()


_TIMELINE_SOURCES = {
    "evidence": (EvidenceItem, "Evidence item"),
    "witness": (WitnessStatementReference, "Witness reference"),
    "hypothesis": (Hypothesis, "Hypothesis"),
}


@router.post("/cases/{case_id}/timeline", response_model=TimelineOut, status_code=201)
def add_timeline(case_id: str, body: TimelineIn, user: CurrentUser, db: DB):
    case = require_case(db, user, case_id, Action.edit)
    if body.t_offset_s is None and body.at is None:
        raise ApiError(422, "time_required", "Give a time offset or a clock time")
    if body.source_type in _TIMELINE_SOURCES:
        if not body.source_id:
            raise ApiError(422, "source_required", f"A {body.source_type} timeline event names its source")
        model, label = _TIMELINE_SOURCES[body.source_type]
        _in_case(db, model, body.source_id, case_id, label)
    _in_case(db, Hypothesis, body.hypothesis_id, case_id, "Hypothesis")
    t = TimelineEvent(case_id=case_id, created_by=user.id, **body.model_dump())
    db.add(t)
    db.flush()
    _audit(db, case, user.id, "timeline.recorded", "timeline_event", t.id, hypothesis_id=body.hypothesis_id)
    return t


@router.get("/cases/{case_id}/timeline", response_model=list[TimelineOut])
def list_timeline(case_id: str, user: CurrentUser, db: DB, hypothesis_id: str | None = None):
    """Recorded events (no hypothesis) plus, if requested, the events proposed by one hypothesis."""
    require_case(db, user, case_id)
    q = select(TimelineEvent).where(TimelineEvent.case_id == case_id)
    if hypothesis_id:
        q = q.where((TimelineEvent.hypothesis_id.is_(None)) | (TimelineEvent.hypothesis_id == hypothesis_id))
    return db.scalars(q.order_by(TimelineEvent.t_offset_s.nulls_last(), TimelineEvent.at.nulls_last())).all()


# ---------------------------------------------------------------- hypotheses
@router.post("/cases/{case_id}/hypotheses", response_model=HypothesisOut, status_code=201)
def add_hypothesis(case_id: str, body: HypothesisIn, user: CurrentUser, db: DB):
    case = require_case(db, user, case_id, Action.edit)
    content = _validate_content(body.content)
    for pid in content["participant_ids"]:
        _in_case(db, VehicleRecord, pid, case_id, "Participant")
    h = Hypothesis(case_id=case_id, label=body.label, content=content, created_by=user.id)
    db.add(h)
    db.flush()
    _audit(db, case, user.id, "hypothesis.created", "hypothesis", h.id, label=h.label)
    return _hyp_out(db, h)


@router.get("/cases/{case_id}/hypotheses", response_model=list[HypothesisOut])
def list_hypotheses(case_id: str, user: CurrentUser, db: DB):
    require_case(db, user, case_id)
    return [
        _hyp_out(db, h)
        for h in db.scalars(
            select(Hypothesis).where(Hypothesis.case_id == case_id).order_by(Hypothesis.created_at)
        )
    ]


@router.get("/hypotheses/{hypothesis_id}", response_model=HypothesisOut)
def get_hypothesis(hypothesis_id: str, user: CurrentUser, db: DB):
    return _hyp_out(db, _hypothesis(db, user, hypothesis_id)[0])


@router.patch("/hypotheses/{hypothesis_id}", response_model=HypothesisOut)
def patch_hypothesis(hypothesis_id: str, body: HypothesisPatch, user: CurrentUser, db: DB):
    h, case = _hypothesis(db, user, hypothesis_id, Action.edit)
    if body.content is not None:
        content = _validate_content(body.content)
        for pid in content["participant_ids"]:
            _in_case(db, VehicleRecord, pid, case.id, "Participant")
        # analysis results attached to the hypothesis are kept; they are not part of the analyst's content
        h.content = {
            **content,
            **{k: v for k, v in h.content.items() if k in ("evidence_fit", "evidence_fits")},
        }
    if body.label is not None:
        h.label = body.label
    if body.status is not None:
        h.status = body.status
    _audit(
        db,
        case,
        user.id,
        "hypothesis.updated",
        "hypothesis",
        h.id,
        content_sha256=hashlib.sha256(json.dumps(h.content, sort_keys=True).encode()).hexdigest(),
    )
    return _hyp_out(db, h)


@router.post("/hypotheses/{hypothesis_id}/links", response_model=LinkOut, status_code=201)
def link_evidence(hypothesis_id: str, body: LinkIn, user: CurrentUser, db: DB):
    h, case = _hypothesis(db, user, hypothesis_id, Action.edit)
    _in_case(db, EvidenceObservation, body.observation_id, case.id, "Observation")
    if db.scalar(
        select(HypothesisEvidenceLink.id).where(
            HypothesisEvidenceLink.hypothesis_id == h.id,
            HypothesisEvidenceLink.observation_id == body.observation_id,
        )
    ):
        raise ApiError(409, "already_linked", "This observation is already linked to the hypothesis")
    li = HypothesisEvidenceLink(hypothesis_id=h.id, created_by=user.id, **body.model_dump())
    db.add(li)
    db.flush()
    _audit(
        db,
        case,
        user.id,
        "hypothesis.linked",
        "hypothesis",
        h.id,
        observation_id=body.observation_id,
        relation=body.relation,
    )
    return li


@router.delete("/hypotheses/{hypothesis_id}/links/{observation_id}", status_code=204)
def unlink_evidence(hypothesis_id: str, observation_id: str, user: CurrentUser, db: DB):
    h, case = _hypothesis(db, user, hypothesis_id, Action.edit)
    li = db.scalar(
        select(HypothesisEvidenceLink).where(
            HypothesisEvidenceLink.hypothesis_id == h.id,
            HypothesisEvidenceLink.observation_id == observation_id,
        )
    )
    if li is None:
        raise ApiError(404, "not_found", "Link not found")
    db.delete(li)
    _audit(db, case, user.id, "hypothesis.unlinked", "hypothesis", h.id, observation_id=observation_id)


@router.post("/hypotheses/{hypothesis_id}/analyses", status_code=201)
def attach_analysis(hypothesis_id: str, body: AnalysisIn, user: CurrentUser, db: DB):
    """Record a completed collision-engine run as an analysis of this hypothesis (keeps its provenance)."""
    h, case = _hypothesis(db, user, hypothesis_id, Action.edit)
    run = db.get(SimulationRun, body.run_id)
    if run is None or run.project_id != case.project_id or run.case_id != case.id:
        raise ApiError(404, "not_found", "Run not found in this case")
    if run.state != "completed":
        raise ApiError(422, "run_not_completed", f"Run is {run.state}")
    if run.engine_id not in ("virtualcrash", "pccrash"):
        raise ApiError(422, "not_collision_run", "Only collision-engine runs analyse hypotheses")
    from parallel_contracts.scenario import ScenarioContent
    from parallel_core.models import ScenarioRevision

    sc = ScenarioContent.model_validate(db.get(ScenarioRevision, run.scenario_revision_id).content)  # type: ignore[union-attr]
    if getattr(sc.controls, "hypothesis_id", None) != h.id:
        raise ApiError(422, "hypothesis_mismatch", "The run's scenario analyses a different hypothesis")
    env = json.loads(get_storage().get_bytes(run.result_envelope_key)) if run.result_envelope_key else {}
    imported = env.get("imported") or {}
    rec = ExternalAnalysisRecord(
        case_id=case.id,
        hypothesis_id=h.id,
        run_id=run.id,
        engine_id=run.engine_id,
        external_installation=run.external_installation or imported.get("external_installation", "unknown"),
        analyst=(
            importer.display_name
            if (importer := db.get(User, imported.get("imported_by") or ""))
            else user.display_name
        ),
        method=f"{run.engine_id} reconstruction imported through a file exchange package "
        f"(engine version reported: {run.engine_version or 'not reported'})",
        supplied_provenance=imported.get("supplied_provenance", ""),
        package_sha256=imported.get("package_sha256"),
        imported_by=user.id,
    )
    db.add(rec)
    fit_art = db.scalar(
        select(RunArtifact).where(RunArtifact.run_id == run.id, RunArtifact.name == "evidence_fit.json")
    )
    fit = json.loads(get_storage().get_bytes(fit_art.storage_key)) if fit_art else None
    if fit is not None:
        entry = {
            "run_id": run.id,
            "source": fit["source"],
            "source_label": PLAYBACK_LABELS[PlaybackSource(fit["source"])],
            "rms_residual_m": fit["rms_residual_m"],
            "targets_within_uncertainty": fit["targets_within_uncertainty"],
            "targets_total": fit["targets_total"],
            "definition": fit["definition"],
            "summary": (
                f"RMS residual {fit['rms_residual_m']:.2f} m; {fit['targets_within_uncertainty']} of "
                f"{fit['targets_total']} targets within their uncertainty (run {run.id})"
            )
            if fit["rms_residual_m"] is not None
            else "No modelled positions for the evidence targets",
        }
        h.content = {
            **h.content,
            "evidence_fit": entry,
            "evidence_fits": [
                *[e for e in h.content.get("evidence_fits", []) if e["run_id"] != run.id],
                entry,
            ],
        }
    db.flush()
    _audit(
        db,
        case,
        user.id,
        "hypothesis.analysis_attached",
        "hypothesis",
        h.id,
        run_id=run.id,
        package_sha256=rec.package_sha256,
    )
    return {"external_analysis_id": rec.id, "evidence_fit": h.content.get("evidence_fit")}


@router.get("/cases/{case_id}/external-analyses")
def list_analyses(case_id: str, user: CurrentUser, db: DB):
    require_case(db, user, case_id)
    rows = db.scalars(select(ExternalAnalysisRecord).where(ExternalAnalysisRecord.case_id == case_id)).all()
    return [
        {
            k: getattr(r, k)
            for k in (
                "id",
                "hypothesis_id",
                "run_id",
                "engine_id",
                "external_installation",
                "analyst",
                "method",
                "supplied_provenance",
                "package_sha256",
                "imported_by",
                "created_at",
            )
        }
        for r in rows
    ]


@router.get("/cases/{case_id}/hypotheses-comparison")
def compare_hypotheses(case_id: str, user: CurrentUser, db: DB):
    """Side-by-side evidence links and defined residuals. No probabilities of truth are computed."""
    require_case(db, user, case_id)
    out = []
    for h in db.scalars(
        select(Hypothesis).where(Hypothesis.case_id == case_id).order_by(Hypothesis.created_at)
    ):
        links = db.scalars(
            select(HypothesisEvidenceLink).where(HypothesisEvidenceLink.hypothesis_id == h.id)
        ).all()
        fit = h.content.get("evidence_fit") or {}
        out.append(
            {
                "hypothesis_id": h.id,
                "label": h.label,
                "status": h.status,
                "supporting": [li.observation_id for li in links if li.relation == "supports"],
                "contradicting": [li.observation_id for li in links if li.relation == "contradicts"],
                "unresolved_questions": h.content.get("unresolved_questions", []),
                "rms_residual_m": fit.get("rms_residual_m"),
                "targets_within_uncertainty": fit.get("targets_within_uncertainty"),
                "targets_total": fit.get("targets_total"),
                "analysis_run_id": fit.get("run_id"),
            }
        )
    return {
        "hypotheses": out,
        "basis": "Hypotheses are compared by their evidence links and by the defined RMS residual between "
        "modelled and measured evidence targets. Parallel does not compute probabilities that a "
        "hypothesis is true and does not determine fault.",
    }


@router.get("/hypotheses/{hypothesis_id}/playback")
def playback(hypothesis_id: str, user: CurrentUser, db: DB):
    """Playback sources for a hypothesis, each labelled with what it is."""
    h, case = _hypothesis(db, user, hypothesis_id)
    sources = []
    for e in db.scalars(
        select(EvidenceItem).where(EvidenceItem.case_id == case.id, EvidenceItem.kind == "video")
    ):
        sources.append(
            {
                "source": "recorded_video",
                "label": PLAYBACK_LABELS[PlaybackSource.recorded_video],
                "evidence_item_id": e.id,
                "title": e.title,
                "hypothesis_id": None,
                "note": "Recorded evidence; not specific to any hypothesis.",
            }
        )
    for rec in db.scalars(select(ExternalAnalysisRecord).where(ExternalAnalysisRecord.hypothesis_id == h.id)):
        if rec.run_id is None:
            continue
        run = db.get(SimulationRun, rec.run_id)
        src = (
            PlaybackSource.imported_reconstruction
            if run and run.execution_mode == "file_exchange"
            else PlaybackSource.engine_trajectory
        )
        arts = db.scalars(
            select(RunArtifact).where(RunArtifact.run_id == rec.run_id, RunArtifact.kind == "trajectory")
        ).all()
        sources.append(
            {
                "source": src.value,
                "label": PLAYBACK_LABELS[src],
                "hypothesis_id": h.id,
                "run_id": rec.run_id,
                "engine_id": rec.engine_id,
                "external_installation": rec.external_installation,
                "trajectory_artifacts": [a.name for a in arts],
            }
        )
    illus = h.content.get("illustration")
    if illus:
        sources.append(
            {
                "source": "analyst_illustration",
                "label": PLAYBACK_LABELS[PlaybackSource.analyst_illustration],
                "hypothesis_id": h.id,
            }
        )
    return {"hypothesis_id": h.id, "label": h.label, "sources": sources}


# ---------------------------------------------------------------- case analysis model
@router.post("/cases/{case_id}/collision-model", status_code=201)
def collision_model(case_id: str, body: CollisionModelIn, user: CurrentUser, db: DB):
    """Build a case-scoped collision model whose evidence targets are this case's own observations."""
    from parallel_contracts.domain import DomainModelEnvelope
    from parallel_contracts.domain.collision import CollisionCaseModel, CollisionVehicle, EvidenceTarget
    from parallel_contracts.geometry import LineStringGeom
    from parallel_contracts.provenance import ProvenanceKind
    from parallel_core.models import DomainModel
    from parallel_core.revisions import create_model_revision

    from .models import validate_envelope

    case = require_case(db, user, case_id, Action.edit)
    problems: list[str] = []
    vehicles = []
    for vid in body.vehicle_record_ids:
        v = _in_case(db, VehicleRecord, vid, case_id, "Vehicle record")
        missing = [f for f in ("mass_kg", "length_m", "width_m") if getattr(v, f) is None]
        if missing:
            problems.append(f"vehicle {v.label}: missing {', '.join(missing)}")
            continue
        vehicles.append(
            CollisionVehicle(
                id=v.id,
                vehicle_record_id=v.id,
                label=v.label,
                mass_kg=v.mass_kg,
                length_m=v.length_m,
                width_m=v.width_m,
                wheelbase_m=v.wheelbase_m,
                provenance=Provenance.model_validate(v.provenance),
            )
        )
    targets = []
    for t in body.targets:
        o = _in_case(db, EvidenceObservation, t.observation_id, case_id, "Observation")
        if t.vehicle_record_id and t.vehicle_record_id not in body.vehicle_record_ids:
            problems.append(f"target {o.id}: vehicle {t.vehicle_record_id} is not in the model")
        unc = o.uncertainty or {}
        if o.statement_type != "observation" or o.position is None:
            problems.append(f"target {o.id}: needs a located observation")
        elif unc.get("kind") != "absolute" or not unc.get("value") or unc.get("unit") not in (None, "m"):
            problems.append(f"target {o.id}: needs an absolute position uncertainty in metres")
        else:
            targets.append(
                EvidenceTarget(
                    id=f"t_{o.id}",
                    observation_id=o.id,
                    vehicle_id=t.vehicle_record_id,
                    kind=t.kind,
                    position=o.position[:2],
                    heading_deg=t.heading_deg,
                    position_uncertainty_m=float(unc["value"]),
                )
            )
    edges = []
    for oid in body.road_edge_observation_ids:
        o = _in_case(db, EvidenceObservation, oid, case_id, "Observation")
        if not o.geometry or o.geometry.get("type") != "LineString":
            problems.append(f"road edge {oid}: needs LineString geometry")
        else:
            edges.append(LineStringGeom.model_validate(o.geometry))
    if problems:
        raise ApiError(
            422,
            "incomplete_case_data",
            "The case data cannot form a collision model yet",
            {"problems": problems},
        )
    content = CollisionCaseModel(
        friction_low=min(body.friction_low, body.friction_high),
        friction_high=max(body.friction_low, body.friction_high),
        friction_provenance=Provenance(
            kind=ProvenanceKind.assumption,
            source=body.friction_basis,
            note="Friction range entered by the investigator",
        ),
        road_edges=edges,
        vehicles=vehicles,
        targets=targets,
    )
    env = DomainModelEnvelope(
        discipline="collision",
        name=body.name,
        scene_revision_id=body.scene_revision_id,
        content=content,
        provenance=Provenance(
            kind=ProvenanceKind.derived,
            derived_from=[o.observation_id for o in targets],
            note=f"Built from case {case.reference} evidence records",
        ),
    )
    dm = DomainModel(project_id=case.project_id, case_id=case.id, name=body.name, discipline="collision")
    db.add(dm)
    db.flush()
    rev = create_model_revision(
        db, dm, env, user.id, validate_envelope(env), message="built from case evidence"
    )
    _audit(
        db,
        case,
        user.id,
        "collision_model.created",
        "domain_model",
        dm.id,
        revision=rev.number,
        targets=len(targets),
    )
    return {
        "domain_model_id": dm.id,
        "revision_id": rev.id,
        "targets": len(targets),
        "vehicles": len(vehicles),
    }


# ---------------------------------------------------------------- defined calculations and reviews
@router.post("/cases/{case_id}/calculations/skid-speed", response_model=ObservationOut, status_code=201)
def skid_speed(case_id: str, body: SkidSpeedIn, user: CurrentUser, db: DB):
    """Skid-to-stop speed range, recorded as an interpretation of the skid-mark observation."""
    case = require_case(db, user, case_id, Action.edit)
    obs = _in_case(db, EvidenceObservation, body.observation_id, case_id, "Observation")
    if obs.kind != "skid_mark" or obs.statement_type != "observation":
        raise ApiError(422, "not_a_skid_mark", "Select a skid-mark observation")
    if obs.measurement_value is None or obs.measurement_unit != "m":
        raise ApiError(422, "length_required", "The skid-mark observation needs a measured length in metres")
    lo, hi = skid_speed_metrics(obs.measurement_value, body.friction_low, body.friction_high)
    text = (
        f"If the vehicle skidded to rest over the measured {obs.measurement_value:.2f} m on a level surface with "
        f"friction coefficient {min(body.friction_low, body.friction_high):.2f}-{max(body.friction_low, body.friction_high):.2f} "
        f"({body.friction_basis}), its speed at the start of the skid was {lo.value:.1f}-{hi.value:.1f} km/h "
        "(v = sqrt(2 mu g d))."
    )
    o = EvidenceObservation(
        case_id=case_id,
        statement_type="interpretation",
        kind="skid_mark",
        text=text,
        evidence_item_id=obs.evidence_item_id,
        based_on=[obs.id],
        position=obs.position,
        geometry=obs.geometry,
        uncertainty={
            "kind": "range",
            "lower": lo.value,
            "upper": hi.value,
            "unit": "km/h",
            "note": "Range from the friction interval only; length measurement uncertainty is not included.",
        },
        limitation="Valid only for a full skid to rest on a level surface, with no impact or other deceleration "
        "during the skid. Not a determination of fault.",
        author_id=user.id,
    )
    db.add(o)
    db.flush()
    _audit(db, case, user.id, "calculation.skid_speed", "evidence_observation", o.id, based_on=obs.id)
    return o


@router.get("/cases/{case_id}/reviews")
def case_reviews(case_id: str, user: CurrentUser, db: DB):
    case = require_case(db, user, case_id)
    ids = [
        *db.scalars(select(EvidenceObservation.id).where(EvidenceObservation.case_id == case_id)),
        *db.scalars(select(Hypothesis.id).where(Hypothesis.case_id == case_id)),
    ]
    rows = (
        db.scalars(
            select(ReviewRecord)
            .where(ReviewRecord.project_id == case.project_id, ReviewRecord.object_id.in_(ids))
            .order_by(ReviewRecord.created_at.desc())
        ).all()
        if ids
        else []
    )
    return [
        {
            k: getattr(r, k)
            for k in (
                "id",
                "object_type",
                "object_id",
                "scope",
                "status",
                "comment",
                "reviewer_id",
                "created_at",
            )
        }
        for r in rows
    ]
