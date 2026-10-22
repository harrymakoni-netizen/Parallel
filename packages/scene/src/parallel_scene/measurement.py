"""Measurement validation (spec section 13.5).

Displays reference distance, reconstructed distance, absolute and relative
error and the measurement source; tolerances are project-specific.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from parallel_contracts.scene import Measurement, MeasurementTolerance
from parallel_contracts.units import convert


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    pa, pb = np.asarray(a, float), np.asarray(b, float)
    if len(pa) == 2:
        pa = np.append(pa, 0.0)
    if len(pb) == 2:
        pb = np.append(pb, 0.0)
    return float(np.linalg.norm(pb - pa))


def evaluate(m: Measurement, tolerance: MeasurementTolerance | None) -> Measurement:
    """Return a copy of the measurement with reconstructed distance and errors filled in."""
    out = m.model_copy(deep=True)
    out.reconstructed_distance_m = distance(m.point_a, m.point_b)
    if m.reference_distance is not None:
        ref = convert(m.reference_distance.value, m.reference_distance.unit, "m")
        out.abs_error_m = abs(out.reconstructed_distance_m - ref)
        out.rel_error = out.abs_error_m / ref if ref > 0 else None
        if tolerance is not None and m.purpose == "independent_check":
            checks = []
            if tolerance.max_abs_error_m is not None:
                checks.append(out.abs_error_m <= tolerance.max_abs_error_m)
            if tolerance.max_rel_error is not None and out.rel_error is not None:
                checks.append(out.rel_error <= tolerance.max_rel_error)
            out.within_tolerance = all(checks) if checks else None
    return out
