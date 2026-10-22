"""Controlled comparisons (/api/v1/comparisons)."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import select

from parallel_core.access import (
    AccessDenied,
    Action,
    can_project,
    require_project,
    require_scoped,
    visible_case_ids,
)
from parallel_core.audit import record as audit
from parallel_core.models import Comparison, SimulationRun

from ..deps import DB, CurrentUser
from ..errors import ApiError
from ..schemas import ComparisonIn, ComparisonOut
from ..services import comparisons as svc

router = APIRouter(tags=["comparisons"])


def load_runs(db, user, project_id: str, ids: list[str]) -> list[SimulationRun]:
    runs = []
    for rid in ids:
        r = db.get(SimulationRun, rid)
        if r is None or r.project_id != project_id:
            raise ApiError(404, "not_found", f"Run {rid} not found in this project")
        require_scoped(db, user, project_id, r.case_id)
        if r.state != "completed":
            raise ApiError(
                422, "run_not_completed", f"Run {rid} is {r.state}; only completed runs can be compared"
            )
        runs.append(r)
    return runs


@router.post("/projects/{project_id}/comparisons", response_model=ComparisonOut, status_code=201)
def create(project_id: str, body: ComparisonIn, user: CurrentUser, db: DB):
    project = require_project(db, user, project_id)
    if not (can_project(db, user, project, Action.edit) or can_project(db, user, project, Action.review)):
        raise AccessDenied("viewers cannot create comparisons", status=403)
    ids = [i for i in body.run_ids if i != body.baseline_run_id]
    if not ids:
        raise ApiError(422, "nothing_to_compare", "Select at least one run besides the baseline")
    base, *others = load_runs(db, user, project_id, [body.baseline_run_id, *ids])
    comp, table, described = svc.build(db, base, others)
    c = Comparison(
        project_id=project_id,
        name=body.name,
        baseline_run_id=base.id,
        run_ids=[base.id, *ids],
        compatibility={**comp, "runs": described},
        table=table,
        created_by=user.id,
    )
    db.add(c)
    db.flush()
    audit(
        db,
        action="comparison.created",
        object_type="comparison",
        object_id=c.id,
        actor_id=user.id,
        project_id=project_id,
        details={"controlled": comp["controlled"]},
    )
    return c


@router.get("/projects/{project_id}/comparisons", response_model=list[ComparisonOut])
def list_(project_id: str, user: CurrentUser, db: DB):
    require_project(db, user, project_id)
    rows = db.scalars(
        select(Comparison).where(Comparison.project_id == project_id).order_by(Comparison.created_at.desc())
    ).all()
    return [c for c in rows if _visible(db, user, c)]


def _visible(db, user, c: Comparison) -> bool:
    cases = {
        r.case_id for r in db.scalars(select(SimulationRun).where(SimulationRun.id.in_(c.run_ids))) if r.case_id
    }
    return cases <= set(visible_case_ids(db, user, c.project_id))


def _get(db, user, cid: str) -> Comparison:
    c = db.get(Comparison, cid)
    if c is None:
        raise ApiError(404, "not_found", "Comparison not found")
    require_project(db, user, c.project_id)
    if not _visible(db, user, c):
        raise ApiError(404, "not_found", "Comparison not found")
    return c


@router.get("/comparisons/{comparison_id}", response_model=ComparisonOut)
def get(comparison_id: str, user: CurrentUser, db: DB):
    return _get(db, user, comparison_id)


@router.get("/comparisons/{comparison_id}/csv")
def export_csv(comparison_id: str, user: CurrentUser, db: DB):
    c = _get(db, user, comparison_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    names = sorted({n for row in c.table for n in row["candidates"]})
    w.writerow(
        [
            "metric_id",
            "entity",
            "unit",
            "baseline_value",
            "baseline_status",
            *[f"{n} {k}" for n in names for k in ("value", "status", "abs_diff", "pct_diff", "note")],
        ]
    )
    for row in c.table:
        b = row["baseline"]
        cells = []
        for n in names:
            x = row["candidates"].get(n, {})
            cells += [
                x.get("value"),
                x.get("status"),
                x.get("abs_diff"),
                x.get("pct_diff"),
                x.get("diff_note"),
            ]
        w.writerow(
            [row["metric_id"], row["entity"] or "", row["unit"], b.get("value"), b.get("status"), *cells]
        )
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="comparison-{c.id}.csv"'},
    )
