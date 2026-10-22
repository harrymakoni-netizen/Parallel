"""Time-series helpers with explicit numerical rules."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def trapezoid_volume(times_s: Sequence[float], rates: Sequence[float]) -> float:
    """Trapezoidal integral of a rate (e.g. m3/s) over time (s)."""
    t = np.asarray(times_s, dtype=float)
    q = np.asarray(rates, dtype=float)
    if len(t) < 2:
        return 0.0
    return float(np.sum((q[1:] + q[:-1]) * 0.5 * np.diff(t)))


def duration_above(times_s: Sequence[float], values: Sequence[float], threshold: float) -> float:
    """Total time (s) of report intervals whose end value exceeds the threshold.

    SWMM report values are instantaneous at the end of each reporting step; an
    interval is counted when the value at its end exceeds the threshold.
    """
    t = np.asarray(times_s, dtype=float)
    v = np.asarray(values, dtype=float)
    if len(t) < 2:
        return 0.0
    dt = np.diff(t)
    return float(np.sum(dt[v[1:] > threshold]))


def summary_stats(values: Sequence[float]) -> dict[str, float]:
    a = np.asarray(values, dtype=float)
    if len(a) == 0:
        return {}
    return {
        "n": float(len(a)),
        "mean": float(a.mean()),
        "sd": float(a.std(ddof=1)) if len(a) > 1 else 0.0,
        "min": float(a.min()),
        "p05": float(np.percentile(a, 5)),
        "p50": float(np.percentile(a, 50)),
        "p95": float(np.percentile(a, 95)),
        "max": float(a.max()),
    }
