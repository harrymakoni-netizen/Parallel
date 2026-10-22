"""Bounded optimisation studies (spec section 23.3).

Each candidate is a scenario revision (origin "optimisation") that is
validated, executed by the real engine and evaluated from its recorded
metrics. Search is deterministic: an exhaustive grid in a fixed order, or
coordinate search that moves only to a better feasible tested point. All
candidates share the base scenario's random seed (common random numbers).
The result is the best *tested* candidate within the budget; nothing is
claimed about untested points.
"""

from __future__ import annotations

import copy
import itertools
import logging
import math
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from parallel_contracts.scenario import ScenarioContent
from parallel_metrics.registry import METRICS

from .db import session_scope, utcnow
from .models import Metric, OptimisationStudy, Project, Scenario, ScenarioRevision, SimulationRun
from .revisions import create_scenario_revision
from .runs import SubmissionError, execute_run, submit_run

log = logging.getLogger(__name__)

MAX_VALUES_PER_VARIABLE = 25
MAX_SWEEPS = 6
FIXED_PATHS = ("settings.seed", "engine_id", "discipline", "is_baseline", "schema_version")
OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12),
}


# ---------------------------------------------------------------- definition handling
def variable_values(v: dict[str, Any]) -> list[Any]:
    if "values" in v:
        return list(v["values"])
    lo, hi, steps = float(v["low"]), float(v["high"]), int(v["steps"])
    if steps < 2 or hi <= lo:
        return [lo]
    vals: list[Any] = [lo + (hi - lo) * i / (steps - 1) for i in range(steps)]
    if v.get("integer"):
        vals = sorted({round(x) for x in vals})
    return vals


def get_path(data: Any, path: str) -> Any:
    cur = data
    for key in path.split("."):
        if isinstance(cur, list):
            if not key.isdigit() or int(key) >= len(cur):
                return None
            cur = cur[int(key)]
        elif isinstance(cur, dict):
            if key not in cur:
                return None
            cur = cur[key]
        else:
            return None
    return cur


def set_path(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = data
    for key in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(key)]
        else:
            if cur.get(key) is None:
                cur[key] = {}
            cur = cur[key]
    if isinstance(cur, list):
        cur[int(parts[-1])] = value
    else:
        cur[parts[-1]] = value


def candidate_content(base: dict[str, Any], assignment: dict[str, Any]) -> ScenarioContent:
    data = copy.deepcopy(base)
    for path, value in assignment.items():
        set_path(data, path, value)
    data["is_baseline"] = False
    return ScenarioContent.model_validate(data)


def check_definition(base: dict[str, Any], d: dict[str, Any]) -> list[str]:
    """Problems that make a study definition unusable (empty list when it is valid)."""
    problems: list[str] = []
    discipline = base.get("discipline")
    m = METRICS.get(d["objective_metric"])
    if m is None:
        problems.append(f"objective metric {d['objective_metric']} is not a documented metric")
    else:
        if m.discipline != discipline:
            problems.append(f"objective metric {m.id} belongs to {m.discipline}, not {discipline}")
        if m.per_entity and not d.get("objective_entity"):
            problems.append(f"objective metric {m.id} is reported per entity: choose an entity")
    for c in d.get("constraints", []):
        if c.get("metric_id") not in METRICS:
            problems.append(f"constraint metric {c.get('metric_id')} is not a documented metric")
        if c.get("op") not in OPS or not isinstance(c.get("value"), int | float):
            problems.append(f"constraint on {c.get('metric_id')}: needs op <=, >= or == and a numeric value")
    paths = [v.get("path", "") for v in d["variables"]]
    if len(set(paths)) != len(paths):
        problems.append("each variable path may appear once")
    for v in d["variables"]:
        path = v.get("path", "")
        if not path.startswith("controls.") or any(path.startswith(f) for f in FIXED_PATHS):
            problems.append(
                f"variable {path!r}: only scenario controls can vary (the seed and engine stay fixed)"
            )
            continue
        try:
            values = variable_values(v)
        except (KeyError, TypeError, ValueError):
            problems.append(f"variable {path}: give 'values' or 'low', 'high' and 'steps'")
            continue
        if not values or len(values) > MAX_VALUES_PER_VARIABLE:
            problems.append(f"variable {path}: 1 to {MAX_VALUES_PER_VARIABLE} values")
            continue
        for value in values[:1] + values[-1:]:
            try:
                candidate_content(base, {path: value})
            except (ValidationError, KeyError, IndexError, ValueError, TypeError, AttributeError) as e:
                problems.append(f"variable {path} = {value!r} does not form a valid scenario: {str(e)[:200]}")
                break
    return problems


def grid_size(d: dict[str, Any]) -> int:
    return math.prod(len(variable_values(v)) for v in d["variables"])


# ---------------------------------------------------------------- evaluation
def _metric(db, run_id: str, metric_id: str, entity: str | None) -> Metric | None:
    q = select(Metric).where(
        Metric.run_id == run_id, Metric.metric_id == metric_id, Metric.replicate.is_(None)
    )
    q = q.where(Metric.entity == entity) if entity else q.where(Metric.entity.is_(None))
    return db.scalar(q)


def _assess(db, run: SimulationRun, d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "run_state": run.state,
        "objective": None,
        "objective_status": "not evaluated",
        "constraints": [],
        "feasible": False,
    }
    if run.state != "completed":
        out["objective_status"] = f"run {run.state}: {run.error_message or ''}".strip()
        return out
    obj = _metric(db, run.id, d["objective_metric"], d.get("objective_entity"))
    if obj is None or obj.status != "available" or obj.value is None:
        out["objective_status"] = obj.status if obj else "missing"
    else:
        out["objective"], out["objective_status"] = obj.value, "available"
    feasible = out["objective_status"] == "available"
    for c in d.get("constraints", []):
        m = _metric(db, run.id, c["metric_id"], c.get("entity"))
        ok = (
            m is not None
            and m.status == "available"
            and m.value is not None
            and OPS[c["op"]](m.value, c["value"])
        )
        out["constraints"].append(
            {
                **c,
                "observed": m.value if m else None,
                "status": "satisfied"
                if ok
                else ("not evaluable" if m is None or m.value is None else "violated"),
            }
        )
        feasible = feasible and ok
    out["feasible"] = feasible
    # trade-off context: every summary metric of the candidate, so nothing hides behind one score
    out["metrics"] = {
        f"{m.metric_id}{'[' + m.entity + ']' if m.entity else ''}": m.value
        for m in db.scalars(select(Metric).where(Metric.run_id == run.id, Metric.replicate.is_(None)))
    }
    return out


def _key(assignment: dict[str, Any]) -> str:
    return repr(sorted(assignment.items()))


class _Study:
    def __init__(self, study_id: str):
        self.id = study_id
        with session_scope() as db:
            s = db.get(OptimisationStudy, study_id)
            assert s is not None
            self.d = dict(s.definition)
            self.project_id, self.user_id, self.name = s.project_id, s.created_by, s.name
            self.base_rev_id = s.base_scenario_revision_id
            self.candidates: list[dict[str, Any]] = list(s.candidates or [])
            base_rev = db.get(ScenarioRevision, self.base_rev_id)
            self.base = dict(base_rev.content)  # type: ignore[union-attr]
            base_sc = db.get(Scenario, base_rev.scenario_id)  # type: ignore[union-attr]
            if not self.d.get("candidate_scenario_id"):
                sc = Scenario(
                    project_id=s.project_id,
                    case_id=base_sc.case_id,  # type: ignore[union-attr]
                    domain_model_id=base_sc.domain_model_id,
                    name=f"{base_sc.name} · candidates ({s.name})",  # type: ignore[union-attr]
                    engine_id=base_sc.engine_id,
                    is_baseline=False,
                )  # type: ignore[union-attr]
                db.add(sc)
                db.flush()
                self.d["candidate_scenario_id"] = sc.id
                s.definition = self.d
        self.memo = {c["key"]: c for c in self.candidates}
        self.evaluations = sum(1 for c in self.candidates if c.get("run_id"))

    def cancelled(self) -> bool:
        with session_scope() as db:
            s = db.get(OptimisationStudy, self.id)
            return s is None or s.status == "cancelling"

    def save(self, **fields: Any) -> None:
        with session_scope() as db:
            s = db.get(OptimisationStudy, self.id)
            assert s is not None
            s.candidates = self.candidates
            for k, v in fields.items():
                setattr(s, k, v)

    def evaluate(self, assignment: dict[str, Any]) -> dict[str, Any]:
        key = _key(assignment)
        if key in self.memo and self.memo[key].get("run_state") in ("completed", "failed", "invalid"):
            return self.memo[key]
        index = len(self.candidates) if key not in self.memo else self.memo[key]["index"]
        cand: dict[str, Any] = {"index": index, "key": key, "values": assignment}
        try:
            content = candidate_content(self.base, assignment)
        except ValidationError as e:
            cand.update(
                run_state="invalid",
                feasible=False,
                objective=None,
                objective_status=f"invalid scenario: {e.errors()[0]['msg']}",
                constraints=[],
            )
            return self._record(cand)
        with session_scope() as db:
            sc = db.get(Scenario, self.d["candidate_scenario_id"])
            rev = create_scenario_revision(
                db,
                sc,
                content,
                self.user_id,
                origin="optimisation",  # type: ignore[arg-type]
                message=f"{self.name}: candidate {index} "
                + ", ".join(f"{k}={v}" for k, v in assignment.items()),
            )
            rev.adopted_by, rev.adopted_at = self.user_id, utcnow()  # authorised by the study's creator
            project = db.get(Project, self.project_id)
            try:
                res = submit_run(
                    db,
                    project=project,
                    scenario_revision=rev,
                    user_id=self.user_id,  # type: ignore[arg-type]
                    idempotency_key=f"opt-{self.id}-{index}",
                    label=f"{self.name} · candidate {index}",
                    optimisation_study_id=self.id,
                    enqueue=False,
                )
            except SubmissionError as e:
                cand.update(
                    run_state="failed",
                    feasible=False,
                    objective=None,
                    constraints=[],
                    objective_status=f"not submitted: {e}",
                    scenario_revision_id=rev.id,
                )
                return self._record(cand)
            cand.update(scenario_revision_id=rev.id, run_id=res.run.id)
            state = res.run.state
        self.evaluations += 1
        if state == "queued":
            execute_run(cand["run_id"])
        with session_scope() as db:
            cand.update(_assess(db, db.get(SimulationRun, cand["run_id"]), self.d))  # type: ignore[arg-type]
        return self._record(cand)

    def _record(self, cand: dict[str, Any]) -> dict[str, Any]:
        self.memo[cand["key"]] = cand
        if cand["index"] < len(self.candidates):
            self.candidates[cand["index"]] = cand
        else:
            self.candidates.append(cand)
        self.save()
        return cand

    def better(self, a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
        """Is candidate a strictly better tested feasible point than b?"""
        if a is None or not a.get("feasible"):
            return False
        if b is None or not b.get("feasible"):
            return True
        return (
            a["objective"] < b["objective"]
            if self.d["direction"] == "minimise"
            else a["objective"] > b["objective"]
        )


def run_study(study_id: str) -> str:
    with session_scope() as db:
        s = db.get(OptimisationStudy, study_id)
        if s is None:
            return "missing"
        if s.status in ("completed", "failed", "cancelled"):
            return s.status
        s.status = "running"
    st = _Study(study_id)
    budget = int(st.d["budget"])
    names = [v["path"] for v in st.d["variables"]]
    domains = [variable_values(v) for v in st.d["variables"]]
    stop = None
    try:
        if st.d["method"] == "grid":
            total = math.prod(len(x) for x in domains)
            for point in itertools.product(*domains):
                if st.evaluations >= budget:
                    stop = f"Budget exhausted after {budget} evaluations ({total} grid points defined)."
                    break
                if st.cancelled():
                    stop = "Cancelled by a user."
                    break
                st.evaluate(dict(zip(names, point, strict=True)))
            else:
                stop = f"All {total} grid points evaluated."
        else:  # coordinate search from the base scenario's values where they are among the options
            current = {}
            for n, dom in zip(names, domains, strict=True):
                b = get_path(st.base, n)
                current[n] = b if b in dom else dom[0]
            best = st.evaluate(dict(current))
            for _sweep in range(MAX_SWEEPS):
                moved = False
                for n, dom in zip(names, domains, strict=True):
                    for value in dom:
                        if value == current[n]:
                            continue
                        if st.evaluations >= budget:
                            stop = f"Budget exhausted after {budget} evaluations."
                            break
                        if st.cancelled():
                            stop = "Cancelled by a user."
                            break
                        cand = st.evaluate({**current, n: value})
                        if st.better(cand, best):
                            best, current, moved = cand, {**current, n: value}, True
                    if stop:
                        break
                if stop:
                    break
                if not moved:
                    stop = (
                        "No single-variable change improved on the current point: coordinate search stopped "
                        "at a local best among tested points."
                    )
                    break
            else:
                stop = f"Stopped after {MAX_SWEEPS} sweeps."
        best_idx = None
        for c in st.candidates:
            if st.better(c, st.candidates[best_idx] if best_idx is not None else None):
                best_idx = c["index"]
        if best_idx is None:
            stop = (
                stop or ""
            ) + " No tested candidate satisfied every constraint with an available objective."
        st.save(
            status="cancelled" if stop and stop.startswith("Cancelled") else "completed",
            best_candidate_index=best_idx,
            stop_reason=stop.strip() if stop else None,
            finished_at=utcnow(),
        )
        return "completed"
    except Exception as e:  # recorded on the study; partial candidates stay available
        log.exception("optimisation %s failed", study_id)
        st.save(status="failed", stop_reason=f"{type(e).__name__}: {e}"[:2000], finished_at=utcnow())
        return "failed"
