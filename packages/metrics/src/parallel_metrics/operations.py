"""Discrete-event operations metrics computed from the SimPy event log.

Event log rows (one per resource request) carry: replication, entity, flow,
step, resource, capacity, request_time, grant_time (or None), release_time (or
None), plus per-entity arrival/finish records. Conservation checks guarantee
that busy units never exceed capacity and every request is either granted or
still waiting at the horizon.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from parallel_contracts.metrics import MetricStatus, MetricValue

from .series import summary_stats


@dataclass(frozen=True)
class RequestRecord:
    replication: int
    entity: str
    flow: str
    step: str
    resource: str
    capacity: int
    request_time: float
    grant_time: float | None
    release_time: float | None


@dataclass(frozen=True)
class EntityRecord:
    replication: int
    entity: str
    flow: str
    arrival_time: float
    finish_time: float | None


class ConservationError(AssertionError):
    pass


def _sweep(events: list[tuple[float, int]]) -> list[tuple[float, int]]:
    """Cumulative level after each event time; ties processed decrements first."""
    events.sort(key=lambda e: (e[0], e[1]))
    level = 0
    out = []
    for t, d in events:
        level += d
        out.append((t, level))
    return out


def check_conservation(requests: Sequence[RequestRecord], horizon_s: float) -> None:
    by_res: dict[tuple[int, str], list[RequestRecord]] = defaultdict(list)
    for r in requests:
        if r.grant_time is not None and r.grant_time < r.request_time - 1e-9:
            raise ConservationError(f"{r.entity}: granted before requested")
        if r.release_time is not None and (r.grant_time is None or r.release_time < r.grant_time - 1e-9):
            raise ConservationError(f"{r.entity}: released without/before grant")
        if r.grant_time is None and r.request_time > horizon_s + 1e-9:
            raise ConservationError(f"{r.entity}: request after horizon")
        by_res[(r.replication, r.resource)].append(r)
    for (rep, res), recs in by_res.items():
        cap = recs[0].capacity
        ev = []
        for r in recs:
            if r.grant_time is not None:
                ev.append((r.grant_time, +1))
                ev.append((r.release_time if r.release_time is not None else float("inf"), -1))
        for t, level in _sweep(ev):
            if level > cap:
                raise ConservationError(
                    f"replication {rep}: resource {res} busy units {level} > capacity {cap} at t={t}"
                )
            if level < 0:
                raise ConservationError(f"replication {rep}: resource {res} negative busy count at t={t}")


def queue_profile(recs: Sequence[RequestRecord], horizon_s: float) -> tuple[float, int]:
    """(time-average queue length, max queue length) for one resource in one replication."""
    ev = []
    for r in recs:
        end = r.grant_time if r.grant_time is not None else horizon_s
        end = min(end, horizon_s)
        if end > r.request_time:
            ev.append((r.request_time, +1))
            ev.append((end, -1))
    if not ev:
        return 0.0, 0
    prof = _sweep(ev)
    area, last_t, last_level, peak = 0.0, 0.0, 0, 0
    for t, level in prof:
        area += last_level * (t - last_t)
        last_t, last_level = t, level
        peak = max(peak, level)
    area += last_level * (horizon_s - last_t)
    return area / horizon_s, peak


def operations_metrics(
    requests: Sequence[RequestRecord],
    entities: Sequence[EntityRecord],
    horizon_s: float,
    replications: int,
    resources: dict[str, int],
    flows: Sequence[str],
) -> list[MetricValue]:
    check_conservation(requests, horizon_s)
    out: list[MetricValue] = []
    # per-flow completion
    for flow in flows:
        comp, unfin, cyc = [], [], []
        for rep in range(replications):
            ents = [
                e for e in entities if e.replication == rep and e.flow == flow and e.arrival_time <= horizon_s
            ]
            done = [e for e in ents if e.finish_time is not None and e.finish_time <= horizon_s]
            comp.append(len(done))
            unfin.append(len(ents) - len(done))
            if done:
                cyc.append(float(np.mean([e.finish_time - e.arrival_time for e in done])))  # type: ignore[operator]
        out.append(
            MetricValue(
                metric_id="ops.completed",
                value=float(np.mean(comp)),
                unit="count",
                entity=flow,
                stats=summary_stats(comp),
            )
        )
        out.append(
            MetricValue(
                metric_id="ops.unfinished",
                value=float(np.mean(unfin)),
                unit="count",
                entity=flow,
                stats=summary_stats(unfin),
            )
        )
        if cyc:
            out.append(
                MetricValue(
                    metric_id="ops.mean_cycle_time",
                    value=float(np.mean(cyc)),
                    unit="s",
                    entity=flow,
                    stats=summary_stats(cyc),
                )
            )
        else:
            out.append(
                MetricValue(
                    metric_id="ops.mean_cycle_time",
                    value=None,
                    unit="s",
                    entity=flow,
                    status=MetricStatus.unavailable,
                    note="No entity completed.",
                )
            )
    # per-resource
    for res, cap in resources.items():
        waits_pooled: list[float] = []
        mean_waits, utils, mean_q, max_q = [], [], [], []
        for rep in range(replications):
            recs = [r for r in requests if r.replication == rep and r.resource == res]
            granted = [r for r in recs if r.grant_time is not None and r.grant_time <= horizon_s]
            w = [r.grant_time - r.request_time for r in granted]  # type: ignore[operator]
            waits_pooled.extend(w)
            if w:
                mean_waits.append(float(np.mean(w)))
            busy = sum(
                min(r.release_time if r.release_time is not None else horizon_s, horizon_s) - r.grant_time
                for r in granted
            )  # type: ignore[operator]
            utils.append(busy / (cap * horizon_s))
            mq, pk = queue_profile(recs, horizon_s)
            mean_q.append(mq)
            max_q.append(pk)
        if mean_waits:
            out.append(
                MetricValue(
                    metric_id="ops.mean_wait",
                    value=float(np.mean(mean_waits)),
                    unit="s",
                    entity=res,
                    stats=summary_stats(mean_waits),
                )
            )
            out.append(
                MetricValue(
                    metric_id="ops.p95_wait",
                    value=float(np.percentile(waits_pooled, 95)),
                    unit="s",
                    entity=res,
                )
            )
        else:
            for mid in ("ops.mean_wait", "ops.p95_wait"):
                out.append(
                    MetricValue(
                        metric_id=mid,
                        value=None,
                        unit="s",
                        entity=res,
                        status=MetricStatus.unavailable,
                        note="No granted requests.",
                    )
                )
        out.append(
            MetricValue(
                metric_id="ops.utilisation",
                value=float(np.mean(utils)),
                unit="1",
                entity=res,
                stats=summary_stats(utils),
            )
        )
        out.append(
            MetricValue(
                metric_id="ops.mean_queue",
                value=float(np.mean(mean_q)),
                unit="count",
                entity=res,
                stats=summary_stats(mean_q),
            )
        )
        out.append(MetricValue(metric_id="ops.max_queue", value=float(max(max_q)), unit="count", entity=res))
    return out
