"""Controlled comparison of runs (spec section 23.1-23.2).

Before comparing, check compatible metrics, units, engine/model versions,
simulation horizon, initial conditions, populations/demand, seed strategy and
boundary conditions, and warn when differences prevent a controlled comparison.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.metrics import MetricStatus, MetricValue
from parallel_contracts.scenario import ScenarioContent
from parallel_core.models import DomainModelRevision, Metric, Scenario, ScenarioRevision, SimulationRun
from parallel_metrics.comparison import compare_metric_sets


def _metrics(db: Session, run_id: str) -> list[MetricValue]:
    return [
        MetricValue(
            metric_id=m.metric_id,
            value=m.value,
            unit=m.unit,
            status=MetricStatus(m.status),
            entity=m.entity,
            note=m.note,
            stats=m.stats,
        )
        for m in db.scalars(select(Metric).where(Metric.run_id == run_id, Metric.replicate.is_(None)))
    ]


def _horizon(sc: ScenarioContent, model: DomainModelEnvelope) -> str:
    c, m = sc.controls, model.content
    if c.kind == "pedestrian":
        return f"{c.max_duration_s:g} s (dt {c.dt_s:g} s)"
    if c.kind == "traffic":
        return f"0-{c.end_s:g} s, warm-up {c.warmup_s:g} s"
    if c.kind == "drainage":
        return f"{m.start_datetime} to {m.end_datetime}"  # type: ignore[union-attr]
    if c.kind == "operations":
        return f"{m.horizon_s:g} s x {c.replications} replications"  # type: ignore[union-attr]
    if c.kind == "water_supply":
        return f"{m.duration_s:g} s"  # type: ignore[union-attr]
    if c.kind == "cfd":
        return f"steady, {c.iterations} iterations"
    return "n/a"


def _boundary(sc: ScenarioContent, model: DomainModelEnvelope) -> str:
    c, m = sc.controls, model.content
    if c.kind == "drainage":
        return ", ".join(f"{o.id}:{o.type}" for o in m.outfalls)  # type: ignore[union-attr]
    if c.kind == "cfd":
        return f"inlet {c.inlet_speed_mps or m.inlet_speed_mps:g} m/s"  # type: ignore[union-attr]
    if c.kind == "water_supply":
        return ", ".join(f"{r.id}:{r.head_m:g} m" for r in m.reservoirs)  # type: ignore[union-attr]
    return "n/a"


def describe_run(db: Session, run: SimulationRun) -> dict[str, Any]:
    sc_rev = db.get(ScenarioRevision, run.scenario_revision_id)
    scenario = db.get(Scenario, run.scenario_id)
    model_rev = db.get(DomainModelRevision, run.domain_model_revision_id)
    sc = ScenarioContent.model_validate(sc_rev.content)  # type: ignore[union-attr]
    model = DomainModelEnvelope.model_validate(model_rev.content)  # type: ignore[union-attr]
    summary = run.result_summary or {}
    population = (
        summary.get("population_positions_sha256")
        or summary.get("demand_hash")
        or summary.get("rainfall_hash")
    )
    return {
        "run_id": run.id,
        "label": f"{scenario.name} (rev {sc_rev.number})",  # type: ignore[union-attr]
        "scenario_name": scenario.name,
        "scenario_revision": sc_rev.number,  # type: ignore[union-attr]
        "engine": run.engine_id,
        "engine_version": run.engine_version,
        "adapter_version": run.adapter_version,
        "model_revision_id": run.domain_model_revision_id,
        "discipline": sc.discipline.value,
        "horizon": _horizon(sc, model),
        "boundary": _boundary(sc, model),
        "seed": run.seed,
        "population_or_demand": population,
        "initial_conditions": summary.get("initial_conditions"),
        "model_change_notes": run.model_change_notes,
        "execution_mode": run.execution_mode,
        "is_baseline": sc.is_baseline,
        "label_note": run.label,
        "assumptions": [a.model_dump(mode="json") for a in sc.assumptions],
    }


def compatibility(base: dict, others: list[dict]) -> dict[str, Any]:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, severity: str = "warning") -> None:
        checks.append({"check": name, "status": "ok" if ok else severity, "detail": detail})

    for o in others:
        who = o["label"]
        add(
            "Discipline",
            o["discipline"] == base["discipline"],
            f"{who}: {o['discipline']} vs {base['discipline']}",
            "error",
        )
        add("Engine", o["engine"] == base["engine"], f"{who}: {o['engine']} vs {base['engine']}")
        add(
            "Engine version",
            o["engine_version"] == base["engine_version"],
            f"{who}: {o['engine_version']} vs {base['engine_version']}",
        )
        add(
            "Adapter version",
            o["adapter_version"] == base["adapter_version"],
            f"{who}: {o['adapter_version']} vs {base['adapter_version']}",
        )
        add(
            "Simulation horizon",
            o["horizon"] == base["horizon"],
            f"{who}: {o['horizon']} vs {base['horizon']}",
        )
        add(
            "Initial conditions",
            o["initial_conditions"] == base["initial_conditions"],
            f"{who}: initial conditions {'match' if o['initial_conditions'] == base['initial_conditions'] else 'differ'}",
        )
        same_pop = o["population_or_demand"] == base["population_or_demand"]
        add(
            "Population / demand / rainfall",
            same_pop,
            f"{who}: {'identical' if same_pop else 'different'} materialised population, demand or rainfall "
            f"({(o['population_or_demand'] or 'n/a')[:12]} vs {(base['population_or_demand'] or 'n/a')[:12]})",
            "info" if base["discipline"] == "drainage" else "warning",
        )
        add("Seed strategy", o["seed"] == base["seed"], f"{who}: seed {o['seed']} vs {base['seed']}")
        add(
            "Boundary conditions",
            o["boundary"] == base["boundary"],
            f"{who}: {o['boundary']} vs {base['boundary']}",
        )
        add(
            "Model revision",
            o["model_revision_id"] == base["model_revision_id"],
            f"{who}: {'same' if o['model_revision_id'] == base['model_revision_id'] else 'different'} domain model revision "
            "(layout alternatives use different revisions)",
            "info",
        )
    controlled = not any(c["status"] in ("warning", "error") for c in checks)
    return {
        "controlled": controlled,
        "checks": checks,
        "summary": "Controlled comparison: only the scenario changes differ."
        if controlled
        else "Not fully controlled: review the flagged differences before attributing outcome changes to the scenario.",
    }


def build(
    db: Session, baseline: SimulationRun, others: list[SimulationRun]
) -> tuple[dict, list[dict], list[dict]]:
    base = describe_run(db, baseline)
    descs = [describe_run(db, r) for r in others]
    comp = compatibility(base, descs)
    labels = {}
    for d in descs:
        lab = d["label"]
        while lab in labels.values() or lab == base["label"]:
            lab += "*"
        labels[d["run_id"]] = lab
    table = compare_metric_sets(_metrics(db, baseline.id), {labels[r.id]: _metrics(db, r.id) for r in others})
    # Unfinished-demand guard (spec 20.4): flag lower travel times that coincide with fewer completed trips.
    for row in table:
        if row["metric_id"] == "traffic.mean_travel_time":
            for name, cell in row["candidates"].items():
                comp_rows = [r for r in table if r["metric_id"] == "traffic.completion_ratio"]
                if comp_rows and cell.get("abs_diff") is not None and cell["abs_diff"] < 0:
                    cr = comp_rows[0]["candidates"].get(name, {})
                    if (cr.get("abs_diff") or 0) < -0.01:
                        cell["diff_note"] = (
                            "Lower mean travel time coincides with a lower completion ratio: fewer "
                            "vehicles completed their trips, so the difference is not an improvement."
                        )
    comp["candidate_labels"] = {lab: rid for rid, lab in labels.items()}
    return comp, table, [base, *descs]
