"""Controlled comparison of metric values (spec section 23).

Shows absolute values, absolute differences and percentage differences only
where mathematically meaningful: no division by zero, no percentages for
unattained/unavailable values, unit-checked.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from parallel_contracts.metrics import MetricStatus, MetricValue
from parallel_contracts.units import UnitError, convert


def _key(m: MetricValue) -> tuple[str, str | None]:
    return (m.metric_id, m.entity)


def compare_metric_sets(
    baseline: Sequence[MetricValue], candidates: dict[str, Sequence[MetricValue]]
) -> list[dict[str, Any]]:
    """Rows keyed by (metric, entity) with baseline value and per-candidate differences."""
    base = {_key(m): m for m in baseline if m.replicate is None}
    keys = list(base)
    for vals in candidates.values():
        for m in vals:
            if m.replicate is None and _key(m) not in base:
                keys.append(_key(m))
    seen = set()
    rows = []
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        b = base.get(k)
        row: dict[str, Any] = {
            "metric_id": k[0],
            "entity": k[1],
            "unit": b.unit if b else None,
            "baseline": _cell(b),
            "candidates": {},
        }
        for name, vals in candidates.items():
            c = next((m for m in vals if _key(m) == k and m.replicate is None), None)
            row["candidates"][name] = _diff(b, c)
            if row["unit"] is None and c is not None:
                row["unit"] = c.unit
        rows.append(row)
    return rows


def _cell(m: MetricValue | None) -> dict[str, Any]:
    if m is None:
        return {"value": None, "status": "missing", "note": "Metric not produced by this run."}
    return {"value": m.value, "status": m.status.value, "note": m.note, "stats": m.stats}


def _diff(b: MetricValue | None, c: MetricValue | None) -> dict[str, Any]:
    cell = _cell(c)
    cell["abs_diff"] = None
    cell["pct_diff"] = None
    cell["diff_note"] = None
    if b is None or c is None:
        cell["diff_note"] = "No difference: metric missing in one run."
        return cell
    if (
        b.status != MetricStatus.available
        or c.status != MetricStatus.available
        or b.value is None
        or c.value is None
    ):
        cell["diff_note"] = "No difference: value unattained or unavailable in at least one run."
        return cell
    cv = c.value
    if c.unit != b.unit:
        try:
            cv = convert(c.value, c.unit, b.unit)
        except UnitError:
            cell["diff_note"] = f"Incompatible units {c.unit} vs {b.unit}."
            return cell
    cell["abs_diff"] = cv - b.value
    if b.value == 0:
        cell["diff_note"] = "Percentage difference not shown: baseline value is zero."
    elif b.unit in ("degC",):
        cell["diff_note"] = "Percentage difference not meaningful for interval-scale units."
    else:
        cell["pct_diff"] = 100.0 * (cv - b.value) / abs(b.value)
    return cell
