"""Evacuation accounting and timing metrics (spec section 17.4).

Accounting identity for a completed snapshot (categories mutually exclusive):

    initial population = evacuated + unreachable + remaining

T-percentiles are measured against the *initial population* (unreachable people
included), so T100 is unattained whenever anyone is unreachable or remaining.
An unattained clearance time is reported as null, never as the last exit time.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from parallel_contracts.metrics import MetricStatus, MetricValue

Status = Literal["evacuated", "unreachable", "remaining"]


@dataclass(frozen=True)
class PersonOutcome:
    agent_id: str
    group_id: str
    status: Status
    start_time_s: float
    exit_time_s: float | None
    exit_id: str | None
    desired_speed_mps: float
    reason: str | None = None


class AccountingError(AssertionError):
    pass


def check_accounting(outcomes: Sequence[PersonOutcome], initial_population: int) -> dict[str, int]:
    ids = [o.agent_id for o in outcomes]
    if len(ids) != len(set(ids)):
        raise AccountingError("a person appears more than once in the outcome records")
    counts = Counter(o.status for o in outcomes)
    total = counts["evacuated"] + counts["unreachable"] + counts["remaining"]
    if total != initial_population or len(outcomes) != initial_population:
        raise AccountingError(
            f"population accounting failed: {initial_population} initial != "
            f"{counts['evacuated']} evacuated + {counts['unreachable']} unreachable + {counts['remaining']} remaining"
        )
    for o in outcomes:
        if o.status == "evacuated" and (o.exit_time_s is None or o.exit_id is None):
            raise AccountingError(f"evacuated person {o.agent_id} has no exit time/exit")
        if o.status != "evacuated" and o.exit_time_s is not None:
            raise AccountingError(f"non-evacuated person {o.agent_id} has an exit time")
    return {k: counts[k] for k in ("evacuated", "unreachable", "remaining")}


def percentile_time(exit_times_sorted: Sequence[float], population: int, pct: int) -> float | None:
    """Smallest exit time at which ceil(pct% x population) people have evacuated; None if unattained."""
    if population <= 0:
        return None
    k = (pct * population + 99) // 100  # integer ceil, avoids float rounding (0.95*100 = 95.000...01)
    if k == 0:
        return 0.0
    if len(exit_times_sorted) < k:
        return None
    return float(exit_times_sorted[k - 1])


def evacuated_curve(
    exit_times: Sequence[float], horizon_s: float, step_s: float = 5.0
) -> list[tuple[float, int]]:
    times = np.sort(np.asarray(exit_times, dtype=float))
    grid = np.arange(0.0, horizon_s + step_s / 2, step_s)
    return [(float(t), int(np.searchsorted(times, t, side="right"))) for t in grid]


def evacuation_metrics(
    outcomes: Sequence[PersonOutcome], initial_population: int, open_exit_ids: Sequence[str]
) -> list[MetricValue]:
    counts = check_accounting(outcomes, initial_population)
    ev = [o for o in outcomes if o.status == "evacuated"]
    exit_times = sorted(o.exit_time_s for o in ev)  # type: ignore[type-var]
    out = [
        MetricValue(metric_id="evac.initial_population", value=initial_population, unit="count"),
        MetricValue(metric_id="evac.evacuated", value=counts["evacuated"], unit="count"),
        MetricValue(metric_id="evac.unreachable", value=counts["unreachable"], unit="count"),
        MetricValue(metric_id="evac.remaining", value=counts["remaining"], unit="count"),
    ]
    for pct in (50, 95, 100):
        t = percentile_time(exit_times, initial_population, pct)
        if t is None:
            out.append(
                MetricValue(
                    metric_id=f"evac.t{pct}",
                    value=None,
                    unit="s",
                    status=MetricStatus.unattained if initial_population > 0 else MetricStatus.not_applicable,
                    note=(
                        f"{len(exit_times)} of {initial_population} people evacuated within the horizon; "
                        f"{pct}% threshold not reached."
                    ),
                )
            )
        else:
            out.append(MetricValue(metric_id=f"evac.t{pct}", value=t, unit="s"))
    travel = np.array([o.exit_time_s - o.start_time_s for o in ev], dtype=float)  # type: ignore[operator]
    if len(travel):
        out.append(MetricValue(metric_id="evac.mean_travel_time", value=float(travel.mean()), unit="s"))
        out.append(
            MetricValue(metric_id="evac.p95_travel_time", value=float(np.percentile(travel, 95)), unit="s")
        )
    else:
        for mid in ("evac.mean_travel_time", "evac.p95_travel_time"):
            out.append(
                MetricValue(
                    metric_id=mid,
                    value=None,
                    unit="s",
                    status=MetricStatus.unavailable,
                    note="Nobody evacuated.",
                )
            )
    usage = Counter(o.exit_id for o in ev)
    for exit_id in open_exit_ids:
        out.append(
            MetricValue(
                metric_id="evac.exit_usage", value=usage.get(exit_id, 0), unit="count", entity=exit_id
            )
        )
    return out


def travel_time_histogram(outcomes: Sequence[PersonOutcome], bin_s: float = 10.0) -> list[dict[str, float]]:
    travel = [o.exit_time_s - o.start_time_s for o in outcomes if o.status == "evacuated"]  # type: ignore[operator]
    if not travel:
        return []
    hi = max(travel)
    edges = np.arange(0.0, hi + bin_s, bin_s)
    hist, edges = np.histogram(travel, bins=edges)
    return [
        {"from_s": float(a), "to_s": float(b), "count": int(c)}
        for a, b, c in zip(edges[:-1], edges[1:], hist, strict=True)
    ]


def slow_movement_fraction(
    agent_ids: np.ndarray,
    times: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
    desired_speed: dict[str, float],
    start_time: dict[str, float],
    threshold: float = 0.3,
) -> float | None:
    """Share of moving-phase samples where speed < threshold x desired speed.

    Inputs are flat trajectory columns. Speeds come from successive samples of the same agent.
    """
    if len(times) < 2:
        return None
    order = np.lexsort((times, agent_ids))
    a, t, x, y = agent_ids[order], times[order], xs[order], ys[order]
    same = a[1:] == a[:-1]
    dt = t[1:] - t[:-1]
    valid = same & (dt > 0)
    if not valid.any():
        return None
    speed = np.hypot(x[1:] - x[:-1], y[1:] - y[:-1])[valid] / dt[valid]
    ids = a[1:][valid]
    tt = t[1:][valid]
    des = np.array([desired_speed[str(i)] for i in ids])
    st = np.array([start_time[str(i)] for i in ids])
    moving = tt > st
    if not moving.any():
        return None
    return float(np.mean(speed[moving] < threshold * des[moving]))
