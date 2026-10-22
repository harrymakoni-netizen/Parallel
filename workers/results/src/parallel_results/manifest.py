"""Assemble the structured content of a report (spec section 27).

The manifest is the single source for every output format: the PDF, the CSV
metrics and the JSON manifest are all rendered from it, so they cannot
disagree. Every result claim carries the identifier of the run it came from.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from parallel_contracts.provenance import PROVENANCE_LABELS, Provenance, ProvenanceKind
from parallel_core.models import (
    Case,
    Comparison,
    DomainModel,
    DomainModelRevision,
    EvidenceItem,
    EvidenceObservation,
    ExternalAnalysisRecord,
    Hypothesis,
    HypothesisEvidenceLink,
    Metric,
    Project,
    Report,
    ReviewRecord,
    Scenario,
    ScenarioRevision,
    SimulationRun,
    TimelineEvent,
    User,
    VehicleRecord,
    WitnessStatementReference,
)
from parallel_core.storage import get_storage
from parallel_metrics.registry import METRICS

MANIFEST_VERSION = "1.0"
PROVENANCE_FIELDS = set(Provenance.model_fields)
PROVENANCE_KINDS = {k.value for k in ProvenanceKind}

NO_FAULT_STATEMENT = (
    "This report presents evidence, measurements and tested hypotheses. It does not determine fault, "
    "liability, intent or legal responsibility; those conclusions belong to the responsible investigator "
    "and the appropriate authority."
)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def provenance_items(data: Any, path: str = "$") -> list[dict[str, Any]]:
    """Every provenance record inside a revision snapshot, with its JSON path."""
    out: list[dict[str, Any]] = []
    if isinstance(data, dict):
        if data.get("kind") in PROVENANCE_KINDS and set(data) <= PROVENANCE_FIELDS:
            out.append(
                {
                    "path": path,
                    **{k: data.get(k) for k in ("kind", "source", "note", "adopted_by")},
                    "uncertainty": data.get("uncertainty"),
                }
            )
            return out
        for k, v in data.items():
            out.extend(provenance_items(v, f"{path}.{k}"))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out.extend(provenance_items(v, f"{path}[{i}]"))
    return out


def summarise_sources(model_content: dict, scenario_content: dict) -> dict[str, Any]:
    items = provenance_items(model_content, "model") + provenance_items(scenario_content, "scenario")
    counts: dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    notable: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for it in items:
        if it["kind"] in ("measured", "imported", "derived", "algorithm_estimate") and not it.get("note"):
            continue
        key = (it["kind"], it.get("source"), it.get("note"))
        if key in seen:
            continue
        seen.add(key)
        notable.append(it)
    assumptions = [
        {
            "id": a.get("id"),
            "statement": a.get("statement"),
            "rationale": a.get("rationale"),
            "applies_to": a.get("applies_to", []),
            "kind": (a.get("provenance") or {}).get("kind"),
        }
        for a in scenario_content.get("assumptions", [])
    ]
    return {
        "counts": dict(sorted(counts.items())),
        "labels": {k: PROVENANCE_LABELS[ProvenanceKind(k)] for k in counts},
        "notable": notable,
        "scenario_assumptions": assumptions,
        "contains_synthetic": counts.get("synthetic", 0) > 0,
        "unadopted_ai": [
            it["path"] for it in items if it["kind"] == "ai_suggested" and not it.get("adopted_by")
        ],
    }


def _envelope(run: SimulationRun) -> dict[str, Any]:
    if not run.result_envelope_key:
        return {}
    try:
        return json.loads(get_storage().get_bytes(run.result_envelope_key))
    except Exception:  # an unreadable envelope is reported, not hidden
        return {"error": "result envelope could not be read"}


def run_section(db: Session, run: SimulationRun, ref: str) -> dict[str, Any]:
    scenario = db.get(Scenario, run.scenario_id)
    sc_rev = db.get(ScenarioRevision, run.scenario_revision_id)
    mrev = db.get(DomainModelRevision, run.domain_model_revision_id)
    model = db.get(DomainModel, mrev.domain_model_id) if mrev else None
    env = _envelope(run)
    metrics = [
        {
            "metric_id": m.metric_id,
            "name": METRICS[m.metric_id].name if m.metric_id in METRICS else m.metric_id,
            "entity": m.entity,
            "value": m.value,
            "unit": m.unit,
            "status": m.status,
            "note": m.note,
            "stats": m.stats,
        }
        for m in db.scalars(
            select(Metric)
            .where(Metric.run_id == run.id, Metric.replicate.is_(None))
            .order_by(Metric.metric_id, Metric.entity)
        )
    ]
    summary = {k: v for k, v in (run.result_summary or {}).items() if k not in ("history", "crosschecks")}
    return {
        "ref": ref,
        "run_id": run.id,
        "label": run.label,
        "scenario": {
            "id": scenario.id,
            "name": scenario.name,
            "is_baseline": scenario.is_baseline,  # type: ignore[union-attr]
            "revision_id": sc_rev.id,
            "revision_number": sc_rev.number,  # type: ignore[union-attr]
            "content_sha256": sc_rev.content_sha256,
            "origin": sc_rev.origin,  # type: ignore[union-attr]
            "adopted_by": sc_rev.adopted_by,
        },  # type: ignore[union-attr]
        "domain_model": {
            "id": model.id if model else None,
            "name": model.name if model else None,
            "discipline": model.discipline if model else None,
            "revision_id": run.domain_model_revision_id,
            "revision_number": mrev.number if mrev else None,
            "content_sha256": mrev.content_sha256 if mrev else None,
        },
        "scene_revision_id": run.scene_revision_id,
        "engine": {
            "engine_id": run.engine_id,
            "engine_version": run.engine_version,
            "adapter_version": run.adapter_version,
        },
        "execution_mode": run.execution_mode,
        "state": run.state,
        "seed": run.seed,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "input_hashes": run.input_hashes,
        "output_hashes": env.get("output_hashes", {}),
        "imported": env.get("imported"),
        "model_change_notes": run.model_change_notes,
        "input_summary": summary,
        "sources": summarise_sources(mrev.content if mrev else {}, sc_rev.content if sc_rev else {}),
        "warnings": list(run.warnings or []),
        "native_crosschecks": (run.result_summary or {}).get("crosschecks", []),
        "metrics": metrics,
        "series_keys": [
            a.get("storage_key") for a in env.get("timeseries", []) if a.get("name") == "series.json"
        ],
    }


def case_section(db: Session, case: Case) -> dict[str, Any]:
    ev = db.scalars(
        select(EvidenceItem).where(EvidenceItem.case_id == case.id).order_by(EvidenceItem.created_at)
    ).all()
    obs = db.scalars(
        select(EvidenceObservation)
        .where(EvidenceObservation.case_id == case.id)
        .order_by(EvidenceObservation.created_at)
    ).all()
    hyps = db.scalars(
        select(Hypothesis).where(Hypothesis.case_id == case.id).order_by(Hypothesis.created_at)
    ).all()
    links = (
        db.scalars(
            select(HypothesisEvidenceLink).where(
                HypothesisEvidenceLink.hypothesis_id.in_([h.id for h in hyps])
            )
        ).all()
        if hyps
        else []
    )

    def o(x: EvidenceObservation) -> dict[str, Any]:
        return {
            "id": x.id,
            "statement_type": x.statement_type,
            "kind": x.kind,
            "text": x.text,
            "evidence_item_id": x.evidence_item_id,
            "measurement_value": x.measurement_value,
            "measurement_unit": x.measurement_unit,
            "uncertainty": x.uncertainty,
            "limitation": x.limitation,
            "based_on": list(x.based_on or []),
            "review_status": x.review_status,
        }

    return {
        "case_id": case.id,
        "reference": case.reference,
        "title": case.title,
        "status": case.status,
        "training": case.training,
        "incident_at": _iso(case.incident_at),
        "location_note": case.location_note,
        "evidence": [
            {
                "id": e.id,
                "kind": e.kind,
                "title": e.title,
                "original_sha256": e.original_sha256,
                "claimed_captured_at": _iso(e.claimed_captured_at),
                "captured_by": e.captured_by,
                "source_asset_id": e.source_asset_id,
            }
            for e in ev
        ],
        "observations": [o(x) for x in obs if x.statement_type == "observation"],
        "interpretations": [o(x) for x in obs if x.statement_type == "interpretation"],
        "hypotheses": [
            {
                "id": h.id,
                "label": h.label,
                "status": h.status,
                "description": (h.content or {}).get("description"),
                "evidence_fit": (h.content or {}).get("evidence_fit"),
                "links": [
                    {"observation_id": li.observation_id, "relation": li.relation, "rationale": li.rationale}
                    for li in links
                    if li.hypothesis_id == h.id
                ],
            }
            for h in hyps
        ],
        "timeline": [
            {
                "id": t.id,
                "t_offset_s": t.t_offset_s,
                "at": _iso(t.at),
                "t_uncertainty_s": t.t_uncertainty_s,
                "description": t.description,
                "source_type": t.source_type,
                "source_id": t.source_id,
                "hypothesis_id": t.hypothesis_id,
            }
            for t in db.scalars(
                select(TimelineEvent)
                .where(TimelineEvent.case_id == case.id)
                .order_by(TimelineEvent.t_offset_s.nulls_last(), TimelineEvent.at)
            )
        ],
        "vehicles": [
            {
                "id": v.id,
                "label": v.label,
                "make": v.make,
                "model": v.model,
                "year": v.year,
                "mass_kg": v.mass_kg,
                "data_source": v.data_source,
            }
            for v in db.scalars(select(VehicleRecord).where(VehicleRecord.case_id == case.id))
        ],
        "witness_references": [
            {"id": w.id, "reference": w.reference, "obtained_at": _iso(w.obtained_at)}
            for w in db.scalars(
                select(WitnessStatementReference).where(WitnessStatementReference.case_id == case.id)
            )
        ],
        "external_analyses": [
            {
                "id": x.id,
                "engine_id": x.engine_id,
                "hypothesis_id": x.hypothesis_id,
                "run_id": x.run_id,
                "external_installation": x.external_installation,
                "analyst": x.analyst,
                "method": x.method,
                "supplied_provenance": x.supplied_provenance,
                "package_sha256": x.package_sha256,
            }
            for x in db.scalars(
                select(ExternalAnalysisRecord).where(ExternalAnalysisRecord.case_id == case.id)
            )
        ],
        "statement": NO_FAULT_STATEMENT,
        "media_policy": "Original evidence media is not embedded in or exported with this report; items are "
        "referenced by identifier and original SHA-256 hash.",
    }


def _case_object_ids(db: Session, case_id: str | None) -> list[str]:
    """Reviewable case records (observations, interpretations, hypotheses) for a case report."""
    if not case_id:
        return []
    return [
        *db.scalars(select(EvidenceObservation.id).where(EvidenceObservation.case_id == case_id)),
        *db.scalars(select(Hypothesis.id).where(Hypothesis.case_id == case_id)),
    ]


def build_manifest(db: Session, report: Report) -> dict[str, Any]:
    project = db.get(Project, report.project_id)
    author = db.get(User, report.author_id) if report.author_id else None
    comparison = db.get(Comparison, report.comparison_id) if report.comparison_id else None
    run_ids = list(report.run_ids)  # the API resolves a comparison's runs into run_ids at creation
    runs = [db.get(SimulationRun, r) for r in run_ids]
    sections = [run_section(db, r, f"R{i + 1}") for i, r in enumerate(runs) if r is not None]
    refs = {s["run_id"]: s["ref"] for s in sections}
    reviews = [
        {
            "object_type": r.object_type,
            "object_id": r.object_id,
            "scope": r.scope,
            "status": r.status,
            "comment": r.comment,
            "reviewer_id": r.reviewer_id,
            "at": _iso(r.created_at),
        }
        for r in db.scalars(
            select(ReviewRecord).where(
                ReviewRecord.project_id == report.project_id,
                ReviewRecord.object_id.in_(
                    [
                        *run_ids,
                        *[s["scenario"]["revision_id"] for s in sections],
                        *[s["domain_model"]["revision_id"] for s in sections],
                        *_case_object_ids(db, report.case_id),
                    ]
                ),
            )
        )
    ]
    warnings = [f"{s['ref']}: {w}" for s in sections for w in s["warnings"]]
    for s in sections:
        warnings += [
            f"{s['ref']}: native cross-check '{c.get('name')}' failed"
            for c in s["native_crosschecks"]
            if not c.get("passed", True)
        ]
        if s["execution_mode"] == "file_exchange":
            warnings.append(
                f"{s['ref']}: results were imported from an external installation "
                f"({(s['imported'] or {}).get('external_installation', 'unknown')}), not executed by Parallel."
            )
        if s["sources"]["contains_synthetic"]:
            warnings.append(
                f"{s['ref']}: inputs include synthetic demonstration material; results do not describe a real site."
            )
        if s["sources"]["unadopted_ai"]:
            warnings.append(f"{s['ref']}: inputs contain AI-suggested values that were never adopted.")
    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "report": {
            "id": report.id,
            "kind": report.kind,
            "title": report.title,
            "review_status": report.review_status,
            "generated_at": datetime.now(UTC).isoformat(),
            "author": {"id": author.id, "name": author.display_name} if author else None,
        },
        "project": {
            "id": project.id,
            "name": project.name,
            "type": project.project_type,  # type: ignore[union-attr]
            "location_label": project.location_label,
            "demo_key": project.demo_key,
        },  # type: ignore[union-attr]
        "runs": sections,
        "run_refs": refs,
        "comparison": None,
        "narrative": {
            "text": report.narrative,
            "origin": report.narrative_origin,
            "adopted_by": report.author_id if report.narrative_origin == "ai_draft_adopted" else None,
        }
        if report.narrative
        else None,
        "reviews": reviews,
        "warnings": warnings,
        "case": None,
        "traceability": "Every result in this report is identified by a run reference (R1, R2, ...) that maps to "
        "a run identifier; each run records its scenario, model and scene revisions, engine and "
        "adapter versions, input hashes and output hashes.",
    }
    if comparison:
        manifest["comparison"] = {
            "id": comparison.id,
            "name": comparison.name,
            "baseline_run_id": comparison.baseline_run_id,
            "baseline_ref": refs.get(comparison.baseline_run_id),
            "controlled": comparison.compatibility.get("controlled"),
            "summary": comparison.compatibility.get("summary"),
            "checks": comparison.compatibility.get("checks", []),
            "table": comparison.table,
            "candidate_refs": {
                label: refs.get(rid)
                for label, rid in comparison.compatibility.get("candidate_labels", {}).items()
            },
        }
    if report.case_id:
        case = db.get(Case, report.case_id)
        if case is not None:
            manifest["case"] = case_section(db, case)
    return manifest
