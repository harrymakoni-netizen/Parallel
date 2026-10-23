"""Narrowly scoped assistant tools (spec 22.4).

Every tool runs as the signed-in user, inside the session's project, through the
same authorisation rules as the API (project roles, explicit case assignment).
No tool executes code, runs SQL or reaches outside the project. Scenario
drafts and report narratives are suggestions: they become model inputs only
when a user adopts them in Parallel.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from parallel_contracts.domain import CONTROLS_DISCIPLINE, DomainModelEnvelope, ScenarioControls
from parallel_contracts.engines import ExecutionMode
from parallel_contracts.provenance import Provenance, ProvenanceKind, find_unadopted_ai_values
from parallel_contracts.scenario import ScenarioContent
from parallel_core.access import Action, require_case, require_scoped, visible_case_ids
from parallel_core.audit import record as audit
from parallel_core.models import (
    DomainModel,
    DomainModelRevision,
    EvidenceItem,
    EvidenceObservation,
    Metric,
    Organisation,
    Project,
    Scenario,
    ScenarioRevision,
    Scene,
    SceneRevision,
    SimulationRun,
    VehicleRecord,
)
from parallel_core.revisions import create_scenario_revision
from parallel_core.runs import SubmissionError, check_engine, submit_run
from parallel_engines.registry import all_adapters, get_adapter
from parallel_metrics.registry import METRICS

MAX_ROWS = 80


class ToolFailure(Exception):
    """A tool could not run; the message is returned to the model as an error result."""


@dataclass
class ToolContext:
    db: Any
    user: Any
    project: Project
    session_id: str
    tool_use_id: str
    provider: str
    model: str
    source_refs: list[dict[str, Any]] = field(default_factory=list)

    def ref(self, kind: str, oid: str, **extra: Any) -> None:
        self.source_refs.append({"type": kind, "id": oid, **extra})

    @property
    def org_settings(self) -> dict[str, Any]:
        org = self.db.get(Organisation, self.project.organisation_id)
        return dict(org.settings or {}) if org else {}


# ---------------------------------------------------------------- schema helpers (strict tool schemas)
def _s(t: str, desc: str) -> dict:
    return {"type": t, "description": desc}


def _n(t: str, desc: str) -> dict:
    return {"anyOf": [{"type": t}, {"type": "null"}], "description": desc}


def _obj(**props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _ids(desc: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": desc}


# ---------------------------------------------------------------- scoping helpers
def _visible(ctx: ToolContext, case_id: str | None) -> bool:
    return case_id is None or case_id in visible_case_ids(ctx.db, ctx.user, ctx.project.id)


def _in_project(
    ctx: ToolContext, obj: Any, label: str, case_id: str | None = None, action: Action = Action.view
):
    if obj is None or obj.project_id != ctx.project.id:
        raise ToolFailure(f"{label} not found in this project")
    require_scoped(ctx.db, ctx.user, ctx.project.id, case_id, action)
    return obj


def _scenario_rev(
    ctx: ToolContext, rev_id: str, action: Action = Action.view
) -> tuple[ScenarioRevision, Scenario]:
    rev = ctx.db.get(ScenarioRevision, rev_id)
    sc = ctx.db.get(Scenario, rev.scenario_id) if rev else None
    _in_project(ctx, sc, "Scenario revision", getattr(sc, "case_id", None), action)
    return rev, sc  # type: ignore[return-value]


def _run(ctx: ToolContext, run_id: str) -> SimulationRun:
    run = ctx.db.get(SimulationRun, run_id)
    return _in_project(ctx, run, "Run", getattr(run, "case_id", None))


def summarise_content(content: dict[str, Any], limit: int = 60) -> dict[str, Any]:
    """Identifiers and names of a model's objects, so scenario controls can reference them."""
    out: dict[str, Any] = {}
    for k, v in content.items():
        if isinstance(v, list) and v and isinstance(v[0], dict) and "id" in v[0]:
            out[k] = {
                "count": len(v),
                "items": [
                    {x: o[x] for x in ("id", "name", "kind", "type", "capacity") if x in o} for o in v[:limit]
                ],
            }
        elif isinstance(v, str | int | float | bool) or v is None:
            out[k] = v
    return out


# ---------------------------------------------------------------- tools
def inspect_project(ctx: ToolContext, _: dict) -> dict:
    db, p = ctx.db, ctx.project
    ctx.ref("project", p.id)
    models = [
        m
        for m in db.scalars(select(DomainModel).where(DomainModel.project_id == p.id))
        if _visible(ctx, m.case_id)
    ]
    scenarios = [
        s for s in db.scalars(select(Scenario).where(Scenario.project_id == p.id)) if _visible(ctx, s.case_id)
    ]
    runs = [
        r
        for r in db.scalars(
            select(SimulationRun)
            .where(SimulationRun.project_id == p.id)
            .order_by(SimulationRun.created_at.desc())
            .limit(40)
        )
        if _visible(ctx, r.case_id)
    ][:20]
    out: dict[str, Any] = {
        "project": {
            "id": p.id,
            "name": p.name,
            "type": p.project_type,
            "description": p.description,
            "location": p.location_label,
            "demonstration": bool(p.demo_key),
        },
        "scenes": [
            {"id": s.id, "name": s.name, "current_revision_id": s.current_revision_id}
            for s in db.scalars(select(Scene).where(Scene.project_id == p.id))
        ],
        "domain_models": [],
        "scenarios": [
            {
                "id": s.id,
                "name": s.name,
                "engine_id": s.engine_id,
                "is_baseline": s.is_baseline,
                "domain_model_id": s.domain_model_id,
                "current_revision_id": s.current_revision_id,
            }
            for s in scenarios
        ],
        "recent_runs": [
            {
                "run_id": r.id,
                "scenario_id": r.scenario_id,
                "engine_id": r.engine_id,
                "state": r.state,
                "label": r.label,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs
        ],
    }
    for m in models:
        rev = db.get(DomainModelRevision, m.current_revision_id) if m.current_revision_id else None
        out["domain_models"].append(
            {
                "id": m.id,
                "name": m.name,
                "discipline": m.discipline,
                "current_revision_id": m.current_revision_id,
                "revision_number": rev.number if rev else None,
                "status": rev.status if rev else None,
                "validation_ok": (rev.validation or {}).get("ok") if rev else None,
            }
        )
    if p.project_type == "crashlens":
        if ctx.org_settings.get("allow_case_media_to_ai"):
            from parallel_core.models import Case

            ids = visible_case_ids(db, ctx.user, p.id)
            out["cases"] = (
                [
                    {"id": c.id, "reference": c.reference, "title": c.title, "training": c.training}
                    for c in db.scalars(select(Case).where(Case.id.in_(ids)))
                ]
                if ids
                else []
            )
        else:
            out["cases"] = (
                "Withheld: organisation policy does not allow case records to be sent to the AI provider."
            )
    return out


def inspect_scene_revision(ctx: ToolContext, args: dict) -> dict:
    rev = ctx.db.get(SceneRevision, args["scene_revision_id"])
    scene = ctx.db.get(Scene, rev.scene_id) if rev else None
    _in_project(ctx, scene, "Scene revision")
    ctx.ref("scene_revision", rev.id)  # type: ignore[union-attr]
    c = rev.content  # type: ignore[union-attr]
    objs = c.get("spatial_objects", [])
    by_layer: dict[str, list] = {}
    for o in objs:
        by_layer.setdefault(o.get("layer", "other"), []).append(
            {
                "id": o.get("id"),
                "name": o.get("name"),
                "kind": o.get("kind"),
                "belongs_to": o.get("belongs_to"),
            }
        )
    return {
        "scene_revision_id": rev.id,
        "number": rev.number,
        "status": rev.status,  # type: ignore[union-attr]
        "hypothetical": c.get("hypothetical"),
        "quality": c.get("quality"),
        "coordinates": c.get("coordinates"),
        "measurements": [
            {
                k: m.get(k)
                for k in (
                    "id",
                    "label",
                    "purpose",
                    "reference_m",
                    "reconstructed_m",
                    "abs_error_m",
                    "within_tolerance",
                    "source",
                )
            }
            for m in c.get("measurements", [])
        ],
        "objects_by_layer": {k: v[:MAX_ROWS] for k, v in by_layer.items()},
        "visual_assets": [
            {
                "id": v.get("id"),
                "kind": v.get("kind"),
                "label": v.get("label"),
                "quality_notes": v.get("quality_notes"),
            }
            for v in c.get("visual_assets", [])
        ],
        "note": "Captured surfaces do not reveal hidden infrastructure, material properties or event sequences.",
    }


def list_domain_models(ctx: ToolContext, args: dict) -> dict:
    q = select(DomainModel).where(DomainModel.project_id == ctx.project.id)
    if args.get("discipline"):
        q = q.where(DomainModel.discipline == args["discipline"])
    out = []
    for m in ctx.db.scalars(q):
        if not _visible(ctx, m.case_id) or not m.current_revision_id:
            continue
        rev = ctx.db.get(DomainModelRevision, m.current_revision_id)
        ctx.ref("domain_model_revision", rev.id)  # type: ignore[union-attr]
        env = rev.content  # type: ignore[union-attr]
        out.append(
            {
                "id": m.id,
                "name": m.name,
                "discipline": m.discipline,
                "revision_id": rev.id,  # type: ignore[union-attr]
                "revision_number": rev.number,
                "status": rev.status,  # type: ignore[union-attr]
                "scene_revision_id": env.get("scene_revision_id"),
                "objects": summarise_content(env.get("content", {})),
            }
        )
    return {"domain_models": out}


def get_engine_capabilities(ctx: ToolContext, args: dict) -> dict:
    out = []
    for a in all_adapters():
        c = a.capability
        if args.get("engine_id") and c.engine_id != args["engine_id"]:
            continue
        avail = {}
        for mode in c.execution_modes:
            chk = check_engine(ctx.db, ctx.project.organisation_id, c.engine_id, ExecutionMode(mode))
            avail[ExecutionMode(mode).value] = {
                "availability": chk.availability.value,
                "details": chk.details[:4],
            }
        out.append(
            {
                "engine_id": c.engine_id,
                "name": c.name,
                "disciplines": [d.value for d in c.disciplines],
                "default_mode": a.default_mode().value,
                "availability": avail,
                "limitations": c.limitations[:6],
                "licence": c.licence.model_dump(mode="json"),
            }
        )
    if not out:
        raise ToolFailure(f"Unknown engine {args.get('engine_id')}")
    return {"engines": out}


def identify_missing_inputs(ctx: ToolContext, args: dict) -> dict:
    dm = ctx.db.get(DomainModel, args["domain_model_id"])
    _in_project(ctx, dm, "Domain model", getattr(dm, "case_id", None))
    rev = ctx.db.get(DomainModelRevision, dm.current_revision_id)  # type: ignore[union-attr]
    ctx.ref("domain_model_revision", rev.id)  # type: ignore[union-attr]
    from parallel_api.routers.models import validate_envelope

    v = validate_envelope(DomainModelEnvelope.model_validate(rev.content))  # type: ignore[union-attr]
    from parallel_results.manifest import provenance_items

    prov = provenance_items(rev.content)  # type: ignore[union-attr]
    return {
        "domain_model_id": dm.id,
        "revision_id": rev.id,  # type: ignore[union-attr]
        "validation": v,
        "assumed_inputs": [p for p in prov if p["kind"] == "assumption"][:MAX_ROWS],
        "synthetic_inputs": sum(1 for p in prov if p["kind"] == "synthetic"),
        "unadopted_ai_values": find_unadopted_ai_values(rev.content),  # type: ignore[union-attr]
        "never_inferred_from_imagery": [
            "rock strength",
            "hidden drainage geometry",
            "unique vehicle speed",
            "traffic demand from road width",
            "material fire properties from colour",
        ],
    }


def get_scenario_schema(ctx: ToolContext, args: dict) -> dict:
    kinds = [k for k, d in CONTROLS_DISCIPLINE.items() if d.value == args["discipline"]]
    if not kinds:
        raise ToolFailure(f"Unknown discipline {args['discipline']}")
    engines = [
        a.capability.engine_id
        for a in all_adapters()
        if args["discipline"] in [d.value for d in a.capability.disciplines]
    ]
    schema = TypeAdapter(ScenarioControls).json_schema()
    defs = schema.get("$defs", {})
    controls = {
        name: d
        for name, d in defs.items()
        if d.get("properties", {}).get("kind", {}).get("const") in kinds
        or d.get("properties", {}).get("kind", {}).get("default") in kinds
    }
    return {
        "discipline": args["discipline"],
        "engines": engines,
        "controls_kind": kinds[0],
        "controls_schema": controls,
        "all_definitions": sorted(defs),
        "scenario_shape": {
            "discipline": args["discipline"],
            "engine_id": engines[0] if engines else None,
            "controls": {"kind": kinds[0], "...": "see controls_schema"},
            "settings": {"seed": 42, "timeout_s": 600},
            "description": "",
            "assumptions": [],
        },
    }


def create_scenario_draft(ctx: ToolContext, args: dict) -> dict:
    dm = ctx.db.get(DomainModel, args["domain_model_id"])
    _in_project(ctx, dm, "Domain model", getattr(dm, "case_id", None), Action.edit)
    try:
        data = json.loads(args["scenario_json"])
        assert isinstance(data, dict)
    except (ValueError, AssertionError) as e:
        raise ToolFailure("scenario_json must be a JSON object") from e
    data.setdefault("discipline", dm.discipline)  # type: ignore[union-attr]
    data["is_baseline"] = False
    data["provenance"] = Provenance(
        kind=ProvenanceKind.ai_suggested,
        source=f"{ctx.provider}:{ctx.model}",
        note=args["explanation"][:2000],
    ).model_dump(mode="json")
    try:
        content = ScenarioContent.model_validate(data)
    except ValidationError as e:
        raise ToolFailure(
            "The draft is not a valid scenario: "
            + "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:8])
        ) from e
    if content.discipline.value != dm.discipline:  # type: ignore[union-attr]
        raise ToolFailure(f"The model is {dm.discipline}; the draft is {content.discipline.value}")  # type: ignore[union-attr]
    from parallel_api.routers.scenarios import _check_engine

    try:
        _check_engine(content)
    except Exception as e:  # ApiError from the shared check
        raise ToolFailure(str(getattr(e, "message", e))) from e
    sc = Scenario(
        project_id=ctx.project.id,
        case_id=dm.case_id,
        domain_model_id=dm.id,
        name=args["name"][:200],  # type: ignore[union-attr]
        engine_id=content.engine_id,
        is_baseline=False,
    )
    ctx.db.add(sc)
    ctx.db.flush()
    rev = create_scenario_revision(
        ctx.db, sc, content, ctx.user.id, origin="ai", message=f"Assistant draft: {args['explanation'][:300]}"
    )
    model_rev = ctx.db.get(DomainModelRevision, dm.current_revision_id)  # type: ignore[union-attr]
    report = get_adapter(sc.engine_id).validate_scenario(
        DomainModelEnvelope.model_validate(model_rev.content), content
    )  # type: ignore[union-attr]
    audit(
        ctx.db,
        action="assistant.scenario_drafted",
        object_type="scenario_revision",
        object_id=rev.id,
        actor_id=ctx.user.id,
        project_id=ctx.project.id,
        details={"session_id": ctx.session_id},
    )
    ctx.ref("scenario_revision", rev.id)
    return {
        "scenario_id": sc.id,
        "scenario_revision_id": rev.id,
        "adopted": False,
        "validation": report.model_dump(mode="json"),
        "next_step": "This is an unadopted draft. A user must review and adopt it in Parallel before it can run.",
    }


def validate_scenario(ctx: ToolContext, args: dict) -> dict:
    rev, sc = _scenario_rev(ctx, args["scenario_revision_id"])
    dm = ctx.db.get(DomainModel, sc.domain_model_id)
    model_rev = ctx.db.get(DomainModelRevision, dm.current_revision_id)  # type: ignore[union-attr]
    adapter = get_adapter(sc.engine_id)
    report = adapter.validate_scenario(
        DomainModelEnvelope.model_validate(model_rev.content),  # type: ignore[union-attr]
        ScenarioContent.model_validate(rev.content),
    )
    ctx.ref("scenario_revision", rev.id)
    return {
        "scenario_revision_id": rev.id,
        "validation": report.model_dump(mode="json"),
        "adopted": rev.origin != "ai" or rev.adopted_by is not None,
    }


def estimate_run_resources(ctx: ToolContext, args: dict) -> dict:
    rev, sc = _scenario_rev(ctx, args["scenario_revision_id"])
    dm = ctx.db.get(DomainModel, sc.domain_model_id)
    model_rev = ctx.db.get(DomainModelRevision, dm.current_revision_id)  # type: ignore[union-attr]
    adapter = get_adapter(sc.engine_id)
    est = adapter.estimate_resources(
        DomainModelEnvelope.model_validate(model_rev.content),  # type: ignore[union-attr]
        ScenarioContent.model_validate(rev.content),
    )
    chk = check_engine(ctx.db, ctx.project.organisation_id, sc.engine_id, adapter.default_mode())
    return {
        "estimate": est.model_dump(mode="json"),
        "availability": chk.availability.value,
        "details": chk.details[:4],
    }


def submit_run_tool(ctx: ToolContext, args: dict) -> dict:
    rev, sc = _scenario_rev(ctx, args["scenario_revision_id"], Action.edit)
    if rev.origin == "ai" and rev.adopted_by is None:
        raise ToolFailure(
            "This scenario is an assistant draft that no user has adopted. Ask the user to review and "
            "adopt it in Parallel first."
        )
    try:
        res = submit_run(
            ctx.db,
            project=ctx.project,
            scenario_revision=rev,
            user_id=ctx.user.id,
            idempotency_key=f"assistant-{ctx.tool_use_id}",
            label="Submitted from the assistant",
        )
    except SubmissionError as e:
        raise ToolFailure(str(e)) from e
    ctx.ref("run", res.run.id)
    return {
        "run_id": res.run.id,
        "state": res.run.state,
        "engine_id": res.run.engine_id,
        "execution_mode": res.run.execution_mode,
        "error": res.run.error_message,
    }


def get_run_status(ctx: ToolContext, args: dict) -> dict:
    r = _run(ctx, args["run_id"])
    ctx.ref("run", r.id)
    return {
        "run_id": r.id,
        "state": r.state,
        "progress": r.progress,
        "message": r.progress_message,
        "error_code": r.error_code,
        "error": r.error_message,
        "attempts": r.attempts,
    }


def get_run_results(ctx: ToolContext, args: dict) -> dict:
    r = _run(ctx, args["run_id"])
    if r.state != "completed":
        return {"run_id": r.id, "state": r.state, "note": "Results exist only for completed runs."}
    q = (
        select(Metric)
        .where(Metric.run_id == r.id, Metric.replicate.is_(None))
        .order_by(Metric.metric_id, Metric.entity)
    )
    if args.get("metric_prefix"):
        q = q.where(Metric.metric_id.startswith(args["metric_prefix"]))
    if args.get("entity"):
        q = q.where(Metric.entity == args["entity"])
    rows = list(ctx.db.scalars(q))
    ctx.ref("run", r.id)
    metrics = []
    for m in rows[:MAX_ROWS]:
        metrics.append(
            {
                "metric_id": m.metric_id,
                "name": METRICS[m.metric_id].name if m.metric_id in METRICS else None,
                "entity": m.entity,
                "value": m.value,
                "unit": m.unit,
                "status": m.status,
                "note": m.note,
                "stats": m.stats,
            }
        )
        ctx.ref("metric", r.id, metric_id=m.metric_id, entity=m.entity)
    summary = r.result_summary or {}
    return {
        "run_id": r.id,
        "engine_id": r.engine_id,
        "engine_version": r.engine_version,
        "adapter_version": r.adapter_version,
        "execution_mode": r.execution_mode,
        "seed": r.seed,
        "scenario_revision_id": r.scenario_revision_id,
        "domain_model_revision_id": r.domain_model_revision_id,
        "metrics": metrics,
        "more_metrics": max(0, len(rows) - MAX_ROWS),
        "warnings": r.warnings,
        "native_crosschecks": [
            {"name": c.get("name"), "passed": c.get("passed")} for c in summary.get("crosschecks", [])
        ],
        "model_change_notes": r.model_change_notes,
    }


def compare_runs(ctx: ToolContext, args: dict) -> dict:
    from parallel_api.routers.comparisons import load_runs
    from parallel_api.services import comparisons as svc

    ids = [i for i in args["run_ids"] if i != args["baseline_run_id"]]
    if not ids:
        raise ToolFailure("Give at least one run besides the baseline")
    try:
        base, *others = load_runs(ctx.db, ctx.user, ctx.project.id, [args["baseline_run_id"], *ids])
    except Exception as e:
        raise ToolFailure(str(getattr(e, "message", e))) from e
    comp, table, described = svc.build(ctx.db, base, others)
    for r in (base, *others):
        ctx.ref("run", r.id)
    return {
        "compatibility": {k: comp[k] for k in ("controlled", "summary", "checks")},
        "runs": [
            {k: d[k] for k in ("run_id", "label", "engine", "seed", "model_change_notes")} for d in described
        ],
        "table": table[:MAX_ROWS],
        "more_rows": max(0, len(table) - MAX_ROWS),
    }


def draft_report(ctx: ToolContext, args: dict) -> dict:
    for rid in args["run_ids"]:
        r = _run(ctx, rid)
        if r.state != "completed":
            raise ToolFailure(f"Run {rid} is {r.state}; reports cover completed runs only")
        ctx.ref("run", r.id)
    return {
        "suggestion": {
            "kind": "report",
            "title": args["title"][:300],
            "run_ids": args["run_ids"],
            "narrative": args["narrative"][:20000],
        },
        "next_step": "Recorded as a suggestion. The narrative becomes part of a report only if a user adopts it.",
    }


def list_case_evidence(ctx: ToolContext, args: dict) -> dict:
    if not ctx.org_settings.get("allow_case_media_to_ai"):
        raise ToolFailure("Organisation policy does not allow case records to be sent to the AI provider.")
    case = require_case(ctx.db, ctx.user, args["case_id"])
    if case.project_id != ctx.project.id:
        raise ToolFailure("Case not found in this project")
    db = ctx.db
    obs = list(db.scalars(select(EvidenceObservation).where(EvidenceObservation.case_id == case.id)))
    basis: dict[str, str] = {}
    vid = args.get("vehicle_record_id")
    if vid:
        v = db.get(VehicleRecord, vid)
        if v is None or v.case_id != case.id:
            raise ToolFailure("Vehicle record not found in this case")
        for m in db.scalars(select(DomainModel).where(DomainModel.case_id == case.id)):
            rev = db.get(DomainModelRevision, m.current_revision_id) if m.current_revision_id else None
            for t in (rev.content or {}).get("content", {}).get("targets", []) if rev else []:
                if t.get("vehicle_id") == vid:
                    basis[t["observation_id"]] = f"evidence target for this vehicle in model {m.name}"
        for o in obs:
            if v.label.lower() in o.text.lower():
                basis.setdefault(o.id, "statement text names this vehicle")
        obs = [o for o in obs if o.id in basis]
    items = {e.id: e for e in db.scalars(select(EvidenceItem).where(EvidenceItem.case_id == case.id))}
    for o in obs:
        ctx.ref("observation", o.id)
    return {
        "case": {"id": case.id, "reference": case.reference, "title": case.title, "training": case.training},
        "evidence_items": [
            {"id": e.id, "kind": e.kind, "title": e.title, "original_sha256": e.original_sha256}
            for e in items.values()
        ],
        "statements": [
            {
                "id": o.id,
                "statement_type": o.statement_type,
                "kind": o.kind,
                "text": o.text,
                "evidence_item_id": o.evidence_item_id,
                "based_on": o.based_on,
                "measurement": [o.measurement_value, o.measurement_unit],
                "uncertainty": o.uncertainty,
                "review_status": o.review_status,
                "association": basis.get(o.id),
            }
            for o in obs[:MAX_ROWS]
        ],
        "note": "Media files are never sent to the assistant. Observations and interpretations are distinct records.",
    }


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[ToolContext, dict], dict]
    suggestion: bool = False  # result is a suggestion that a user may adopt

    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema,
            "strict": True,
        }


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "inspect_project",
            "Summary of the current project: scenes, domain models, scenarios and recent runs, "
            "with identifiers. Start here.",
            _obj(),
            inspect_project,
        ),
        Tool(
            "inspect_scene_revision",
            "Objects, measurements and quality states of one scene revision.",
            _obj(scene_revision_id=_s("string", "Scene revision id")),
            inspect_scene_revision,
        ),
        Tool(
            "list_domain_models",
            "Domain models with their object identifiers (exits, route segments, nodes, "
            "edges, resources...) needed to draft scenario controls.",
            _obj(discipline=_n("string", "Optional discipline filter, e.g. pedestrian, drainage")),
            list_domain_models,
        ),
        Tool(
            "get_engine_capabilities",
            "Engine capabilities, limitations, licence and availability in this organisation.",
            _obj(engine_id=_n("string", "Optional engine id, e.g. jupedsim")),
            get_engine_capabilities,
        ),
        Tool(
            "identify_missing_inputs",
            "Validation issues, assumed and synthetic inputs and unadopted AI values of a domain model.",
            _obj(domain_model_id=_s("string", "Domain model id")),
            identify_missing_inputs,
        ),
        Tool(
            "get_scenario_schema",
            "JSON schema of the scenario controls for a discipline, with the engines that "
            "model it. Read before drafting a scenario.",
            _obj(discipline=_s("string", "Discipline, e.g. pedestrian, traffic, drainage, operations")),
            get_scenario_schema,
        ),
        Tool(
            "create_scenario_draft",
            "Create an unadopted scenario draft for a domain model. scenario_json is a JSON "
            "object with discipline, engine_id, controls, settings and assumptions. The draft cannot run until a user "
            "adopts it.",
            _obj(
                domain_model_id=_s("string", "Domain model id"),
                name=_s("string", "Scenario name"),
                scenario_json=_s("string", "Scenario content as a JSON object string"),
                explanation=_s("string", "Plain-language explanation of the proposed changes"),
            ),
            create_scenario_draft,
            suggestion=True,
        ),
        Tool(
            "validate_scenario",
            "Validate a scenario revision against its model with the engine adapter.",
            _obj(scenario_revision_id=_s("string", "Scenario revision id")),
            validate_scenario,
        ),
        Tool(
            "estimate_run_resources",
            "Resource estimate and engine availability for a scenario revision.",
            _obj(scenario_revision_id=_s("string", "Scenario revision id")),
            estimate_run_resources,
        ),
        Tool(
            "submit_run",
            "Submit an adopted, valid scenario revision for execution by its engine.",
            _obj(scenario_revision_id=_s("string", "Scenario revision id")),
            submit_run_tool,
        ),
        Tool(
            "get_run_status",
            "State and progress of a run.",
            _obj(run_id=_s("string", "Run id")),
            get_run_status,
        ),
        Tool(
            "get_run_results",
            "Recorded metrics (with metric identifiers, units and status), warnings and native "
            "cross-checks of a completed run.",
            _obj(
                run_id=_s("string", "Run id"),
                metric_prefix=_n("string", "Optional metric id prefix, e.g. evac."),
                entity=_n("string", "Optional entity id"),
            ),
            get_run_results,
        ),
        Tool(
            "compare_runs",
            "Controlled comparison of completed runs: compatibility checks and differences.",
            _obj(
                baseline_run_id=_s("string", "Baseline run id"),
                run_ids=_ids("Runs to compare with the baseline"),
            ),
            compare_runs,
        ),
        Tool(
            "draft_report",
            "Suggest a report narrative grounded in completed runs. It is used only if a user adopts it.",
            _obj(
                title=_s("string", "Report title"),
                run_ids=_ids("Completed runs the narrative describes"),
                narrative=_s("string", "Narrative citing run and metric identifiers"),
            ),
            draft_report,
            suggestion=True,
        ),
        Tool(
            "list_case_evidence",
            "CrashLens: evidence records and statements of a case (optionally those associated "
            "with one vehicle). Only available where organisation policy allows it.",
            _obj(
                case_id=_s("string", "Case id"), vehicle_record_id=_n("string", "Optional vehicle record id")
            ),
            list_case_evidence,
        ),
    ]
}


def definitions() -> list[dict]:
    return [t.definition() for t in TOOLS.values()]
