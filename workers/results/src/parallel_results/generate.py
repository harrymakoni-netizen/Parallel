"""Report generation: manifest -> PDF + metrics CSV + JSON manifest, stored and hashed."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from datetime import UTC, datetime
from typing import Any

from parallel_core.audit import record as audit
from parallel_core.db import session_scope
from parallel_core.models import Report, SourceAsset
from parallel_core.storage import get_storage

from .charts import Series
from .manifest import build_manifest
from .pdf import render_pdf

log = logging.getLogger(__name__)


def _series(m: dict[str, Any]) -> list[dict[str, Any]]:
    """Chart specifications from the runs' normalised time series."""
    store = get_storage()
    evac: list[Series] = []
    flood: list[Series] = []
    for s in m["runs"]:
        if not s["series_keys"]:
            continue
        data = json.loads(store.get_bytes(s["series_keys"][0]))
        name = f"{s['ref']} {s['scenario']['name']}"
        if "evacuated_over_time" in data:
            evac.append(Series(name, [(r["t_s"], r["evacuated"]) for r in data["evacuated_over_time"]]))
        if "system" in data:
            flood.append(Series(name, [(r["t_s"] / 3600.0, r["flooding_cms"]) for r in data["system"]]))
    if evac:  # show the evacuation itself, not the idle remainder of the horizon
        last = max(next((t for t, n in reversed(se.points) if n != se.points[-1][1]), 0.0) for se in evac)
        end = last * 1.15 + 5.0
        for se in evac:
            kept = [pt for pt in se.points if pt[0] <= end]
            se.points = kept if len(kept) >= 2 else se.points
    charts = []
    if evac:
        charts.append(
            {
                "title": "People evacuated over time",
                "series": evac,
                "x_label": "Time since alarm (s)",
                "y_label": "People evacuated",
                "x_unit": "s",
                "note": "Cumulative count of people who reached an open exit or refuge (5 s grid), from each "
                "run's normalised outcomes; the table gives the values at the sampled times.",
            }
        )
    if flood:
        charts.append(
            {
                "title": "System flooding rate",
                "series": flood,
                "x_label": "Time since start (h)",
                "y_label": "Flooding (m³/s)",
                "x_unit": "h",
                "note": "Total node flooding reported by SWMM at each report step. Network results only: node "
                "flooding does not define a surface inundation extent.",
            }
        )
    return charts


def _figures(project_id: str, ids: list[str]) -> list[dict[str, Any]]:
    out = []
    with session_scope() as db:
        for aid in ids:
            a = db.get(SourceAsset, aid)
            # never embed evidence media automatically; only verified project images
            if (
                a is None
                or a.project_id != project_id
                or a.kind != "image"
                or a.sensitivity != "normal"
                or a.status != "verified"
            ):
                continue
            out.append(
                {
                    "asset_id": a.id,
                    "sha256": a.sha256,
                    "caption": a.filename,
                    "data": get_storage().get_bytes(a.storage_key),
                }
            )
    return out


def metrics_csv(m: dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "run_ref",
            "run_id",
            "scenario",
            "scenario_revision_id",
            "engine_id",
            "engine_version",
            "metric_id",
            "metric_name",
            "entity",
            "value",
            "unit",
            "status",
            "note",
            "stats",
        ]
    )
    for s in m["runs"]:
        for x in s["metrics"]:
            w.writerow(
                [
                    s["ref"],
                    s["run_id"],
                    s["scenario"]["name"],
                    s["scenario"]["revision_id"],
                    s["engine"]["engine_id"],
                    s["engine"]["engine_version"],
                    x["metric_id"],
                    x["name"],
                    x["entity"] or "",
                    "" if x["value"] is None else repr(x["value"]),
                    x["unit"],
                    x["status"],
                    x["note"] or "",
                    json.dumps(x["stats"]) if x["stats"] else "",
                ]
            )
    return buf.getvalue().encode()


def generate_report(report_id: str) -> str:
    with session_scope() as db:
        rep = db.get(Report, report_id)
        if rep is None:
            return "missing"
        if rep.status == "completed":  # duplicate delivery
            return "completed"
        rep.status = "generating"
        rep.error = None
    try:
        with session_scope() as db:
            rep = db.get(Report, report_id)
            manifest = build_manifest(db, rep)  # type: ignore[arg-type]
            figure_ids = list((rep.manifest or {}).get("request", {}).get("figure_asset_ids", []))  # type: ignore[union-attr]
            project_id = rep.project_id  # type: ignore[union-attr]
        figures = _figures(project_id, figure_ids)
        manifest["figures"] = [{k: f[k] for k in ("asset_id", "sha256", "caption")} for f in figures]
        files = {
            "report.pdf": (render_pdf(manifest, _series(manifest), figures), "application/pdf"),
            "metrics.csv": (metrics_csv(manifest), "text/csv"),
        }
        files["manifest.json"] = (json.dumps(manifest, indent=2, default=str).encode(), "application/json")
        store = get_storage()
        keys, hashes = {}, {}
        for name, (data, media) in files.items():
            key = f"reports/{project_id}/{report_id}/{name}"
            store.put_bytes(key, data, media)
            keys[name] = key
            hashes[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        with session_scope() as db:
            rep = db.get(Report, report_id)
            rep.pdf_key, rep.csv_key, rep.json_key = (
                keys["report.pdf"],
                keys["metrics.csv"],
                keys["manifest.json"],
            )  # type: ignore[union-attr]
            rep.manifest = {**manifest, "files": hashes, "request": (rep.manifest or {}).get("request", {})}  # type: ignore[union-attr]
            rep.status = "completed"  # type: ignore[union-attr]
            rep.generated_at = datetime.now(UTC)  # type: ignore[union-attr]
            audit(
                db,
                action="report.generated",
                object_type="report",
                object_id=report_id,
                actor_id=None,
                project_id=project_id,
                details={"files": hashes},
            )
        return "completed"
    except Exception as e:  # recorded on the report; the task itself does not retry deterministic failures
        log.exception("report %s failed", report_id)
        with session_scope() as db:
            rep = db.get(Report, report_id)
            if rep is not None:
                rep.status = "failed"
                rep.error = f"{type(e).__name__}: {e}"[:2000]
        return "failed"
