"""CrashLens defined calculations.

These are explicitly defined calculations with formulas and inputs (spec 4.3),
not engine reconstructions. They never assign fault and never produce
probabilities of truth.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from parallel_contracts.crashlens import (
    EvidenceFit,
    EvidenceResidual,
    PlaybackSource,
    TrajectorySample,
)
from parallel_contracts.domain.collision import EvidenceTarget
from parallel_contracts.metrics import MetricStatus, MetricValue

G = 9.80665  # m/s2, standard gravity


def skid_to_stop_speed_kmh(skid_length_m: float, friction: float) -> float:
    """v = sqrt(2 mu g d), returned in km/h. Valid only for a full skid to rest on a level surface."""
    if skid_length_m <= 0 or friction <= 0:
        raise ValueError("skid length and friction must be positive")
    return math.sqrt(2.0 * friction * G * skid_length_m) * 3.6


def skid_speed_metrics(
    skid_length_m: float | None, mu_low: float | None, mu_high: float | None
) -> list[MetricValue]:
    if skid_length_m is None or mu_low is None or mu_high is None:
        return [
            MetricValue(
                metric_id=mid,
                value=None,
                unit="km/h",
                status=MetricStatus.unavailable,
                note="Requires a measured skid length and a friction range.",
            )
            for mid in ("crash.skid_speed_low", "crash.skid_speed_high")
        ]
    lo, hi = sorted((mu_low, mu_high))
    return [
        MetricValue(
            metric_id="crash.skid_speed_low",
            value=skid_to_stop_speed_kmh(skid_length_m, lo),
            unit="km/h",
            note=f"d = {skid_length_m:.2f} m, mu = {lo:.2f}",
        ),
        MetricValue(
            metric_id="crash.skid_speed_high",
            value=skid_to_stop_speed_kmh(skid_length_m, hi),
            unit="km/h",
            note=f"d = {skid_length_m:.2f} m, mu = {hi:.2f}",
        ),
    ]


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def evidence_fit(
    hypothesis_id: str,
    source: PlaybackSource,
    trajectories: dict[str, Sequence[TrajectorySample]],
    targets: Sequence[EvidenceTarget],
) -> EvidenceFit:
    """Residuals between modelled trajectories and measured evidence targets.

    * rest_position: last sample of the target vehicle's trajectory.
    * other kinds: closest approach of the target vehicle (or any vehicle if none given).
    """
    residuals: list[EvidenceResidual] = []
    for tg in targets:
        cands = [tg.vehicle_id] if tg.vehicle_id else list(trajectories)
        best: tuple[float, TrajectorySample] | None = None
        for vid in cands:
            traj = trajectories.get(vid or "", [])
            if not traj:
                continue
            samples = [traj[-1]] if tg.kind == "rest_position" else list(traj)
            for s in samples:
                d = math.hypot(s.x - tg.position[0], s.y - tg.position[1])
                if best is None or d < best[0]:
                    best = (d, s)
        if best is None:
            residuals.append(
                EvidenceResidual(
                    target_id=tg.id,
                    kind=tg.kind,
                    vehicle_id=tg.vehicle_id,
                    measured=list(tg.position),
                    modelled=None,
                    distance_m=None,
                    uncertainty_m=tg.position_uncertainty_m,
                    within_uncertainty=None,
                    note="No trajectory for this vehicle.",
                )
            )
            continue
        d, s = best
        herr = None
        if tg.heading_deg is not None and s.heading_deg is not None and tg.kind == "rest_position":
            herr = _angle_diff(s.heading_deg, tg.heading_deg)
        residuals.append(
            EvidenceResidual(
                target_id=tg.id,
                kind=tg.kind,
                vehicle_id=tg.vehicle_id,
                measured=list(tg.position),
                modelled=[s.x, s.y],
                distance_m=d,
                heading_error_deg=herr,
                uncertainty_m=tg.position_uncertainty_m,
                within_uncertainty=d <= tg.position_uncertainty_m,
            )
        )
    ds = [r.distance_m for r in residuals if r.distance_m is not None]
    rms = math.sqrt(sum(d * d for d in ds) / len(ds)) if ds else None
    return EvidenceFit(
        hypothesis_id=hypothesis_id,
        source=source,
        residuals=residuals,
        rms_residual_m=rms,
        targets_within_uncertainty=sum(1 for r in residuals if r.within_uncertainty),
        targets_total=len(residuals),
    )


def evidence_fit_metrics(fit: EvidenceFit) -> list[MetricValue]:
    if fit.rms_residual_m is None:
        return [
            MetricValue(
                metric_id="crash.rms_residual",
                value=None,
                unit="m",
                status=MetricStatus.unavailable,
                note="No targets with a modelled position.",
            )
        ]
    return [
        MetricValue(metric_id="crash.rms_residual", value=fit.rms_residual_m, unit="m"),
        MetricValue(
            metric_id="crash.targets_within_uncertainty",
            value=fit.targets_within_uncertainty,
            unit="count",
            note=f"of {fit.targets_total} targets",
        ),
    ]
