import math

import numpy as np
import pytest

from parallel_contracts.crashlens import PlaybackSource, TrajectorySample
from parallel_contracts.domain.collision import EvidenceTarget
from parallel_contracts.metrics import MetricStatus, MetricValue
from parallel_metrics import registry
from parallel_metrics.collision import evidence_fit, skid_to_stop_speed_kmh
from parallel_metrics.comparison import compare_metric_sets
from parallel_metrics.evacuation import (
    AccountingError,
    PersonOutcome,
    check_accounting,
    evacuation_metrics,
    percentile_time,
    slow_movement_fraction,
)
from parallel_metrics.operations import (
    ConservationError,
    EntityRecord,
    RequestRecord,
    check_conservation,
    operations_metrics,
)
from parallel_metrics.series import duration_above, trapezoid_volume


def _p(i, status, t=None, exit_id=None, start=0.0):
    return PersonOutcome(f"a{i}", "g", status, start, t, exit_id, 1.2)


class TestEvacuation:
    def test_accounting_identity(self):
        outs = [_p(0, "evacuated", 10, "E1"), _p(1, "unreachable"), _p(2, "remaining")]
        assert check_accounting(outs, 3) == {"evacuated": 1, "unreachable": 1, "remaining": 1}

    def test_missing_person_detected(self):
        with pytest.raises(AccountingError):
            check_accounting([_p(0, "evacuated", 10, "E1")], 2)

    def test_duplicate_person_detected(self):
        with pytest.raises(AccountingError):
            check_accounting([_p(0, "evacuated", 10, "E1"), _p(0, "remaining")], 2)

    def test_t100_unattained_when_anyone_unreachable(self):
        outs = [_p(i, "evacuated", 10.0 + i, "E1") for i in range(19)] + [_p(19, "unreachable")]
        ms = {m.metric_id: m for m in evacuation_metrics(outs, 20, ["E1"])}
        assert ms["evac.t100"].value is None and ms["evac.t100"].status == MetricStatus.unattained
        assert ms["evac.t95"].value == 28.0  # ceil(0.95*20) = 19th exit
        assert ms["evac.t50"].value == 19.0  # 10th exit

    def test_unattained_never_replaced_by_last_exit(self):
        outs = [_p(0, "evacuated", 50.0, "E1"), _p(1, "remaining")]
        ms = {m.metric_id: m for m in evacuation_metrics(outs, 2, ["E1"])}
        assert ms["evac.t100"].value is None
        assert ms["evac.t50"].value == 50.0

    def test_integer_ceil_avoids_float_error(self):
        times = list(range(1, 101))
        assert percentile_time(times, 100, 95) == 95.0

    def test_exit_usage_includes_unused_open_exits(self):
        outs = [_p(0, "evacuated", 5, "E1")]
        usage = {
            m.entity: m.value
            for m in evacuation_metrics(outs, 1, ["E1", "E2"])
            if m.metric_id == "evac.exit_usage"
        }
        assert usage == {"E1": 1, "E2": 0}

    def test_slow_movement_fraction(self):
        ids = np.array(["a"] * 3 + ["b"] * 3)
        t = np.array([0.0, 1.0, 2.0] * 2)
        x = np.array([0.0, 1.0, 2.0, 0.0, 0.1, 0.2])
        y = np.zeros(6)
        frac = slow_movement_fraction(ids, t, x, y, {"a": 1.0, "b": 1.0}, {"a": -1.0, "b": -1.0})
        assert frac == 0.5


class TestSeries:
    def test_trapezoid(self):
        assert trapezoid_volume([0, 10, 20], [0, 1, 0]) == 10.0

    def test_duration_above(self):
        assert duration_above([0, 60, 120, 180], [0, 1, 1, 0], 0.5) == 120.0


class TestOperations:
    def _reqs(self):
        return [
            RequestRecord(0, "d1", "f", "unload", "bay", 1, 0.0, 0.0, 10.0),
            RequestRecord(0, "d2", "f", "unload", "bay", 1, 5.0, 10.0, 20.0),
            RequestRecord(0, "d3", "f", "unload", "bay", 1, 15.0, None, None),
        ]

    def test_conservation_and_metrics(self):
        reqs = self._reqs()
        ents = [
            EntityRecord(0, "d1", "f", 0.0, 10.0),
            EntityRecord(0, "d2", "f", 5.0, 20.0),
            EntityRecord(0, "d3", "f", 15.0, None),
        ]
        ms = operations_metrics(reqs, ents, 25.0, 1, {"bay": 1}, ["f"])
        by = {(m.metric_id, m.entity): m.value for m in ms}
        assert by[("ops.completed", "f")] == 2
        assert by[("ops.unfinished", "f")] == 1
        assert by[("ops.mean_wait", "bay")] == 2.5
        assert by[("ops.utilisation", "bay")] == 20.0 / 25.0
        # queue: d2 waits 5-10 (1), d3 waits 15-25 (1) -> area 15 / 25
        assert math.isclose(by[("ops.mean_queue", "bay")], 15.0 / 25.0)
        assert by[("ops.max_queue", "bay")] == 1

    def test_capacity_violation_detected(self):
        reqs = [
            RequestRecord(0, "d1", "f", "s", "bay", 1, 0.0, 0.0, 10.0),
            RequestRecord(0, "d2", "f", "s", "bay", 1, 1.0, 1.0, 5.0),
        ]
        with pytest.raises(ConservationError):
            check_conservation(reqs, 20.0)


class TestComparison:
    def test_no_percentage_for_zero_baseline_or_unattained(self):
        base = [
            MetricValue(metric_id="evac.remaining", value=0, unit="count"),
            MetricValue(metric_id="evac.t100", value=None, unit="s", status="unattained"),
        ]
        cand = [
            MetricValue(metric_id="evac.remaining", value=3, unit="count"),
            MetricValue(metric_id="evac.t100", value=120.0, unit="s"),
        ]
        rows = {r["metric_id"]: r for r in compare_metric_sets(base, {"alt": cand})}
        assert rows["evac.remaining"]["candidates"]["alt"]["abs_diff"] == 3
        assert rows["evac.remaining"]["candidates"]["alt"]["pct_diff"] is None
        assert rows["evac.t100"]["candidates"]["alt"]["abs_diff"] is None

    def test_percentage(self):
        base = [MetricValue(metric_id="traffic.mean_travel_time", value=200.0, unit="s")]
        cand = [MetricValue(metric_id="traffic.mean_travel_time", value=150.0, unit="s")]
        row = compare_metric_sets(base, {"alt": cand})[0]
        assert row["candidates"]["alt"]["pct_diff"] == -25.0


class TestCollision:
    def test_skid_formula(self):
        # 30 m skid, mu 0.7 -> sqrt(2*0.7*9.80665*30) m/s
        assert math.isclose(skid_to_stop_speed_kmh(30, 0.7), math.sqrt(2 * 0.7 * 9.80665 * 30) * 3.6)

    def test_evidence_fit(self):
        traj = {"V1": [TrajectorySample(t_s=0, x=0, y=0), TrajectorySample(t_s=2, x=10, y=1, heading_deg=95)]}
        targets = [
            EvidenceTarget(
                id="t1",
                observation_id="o1",
                vehicle_id="V1",
                kind="rest_position",
                position=[10, 0],
                heading_deg=90,
                position_uncertainty_m=1.5,
            ),
            EvidenceTarget(
                id="t2",
                observation_id="o2",
                vehicle_id="V2",
                kind="rest_position",
                position=[0, 5],
                position_uncertainty_m=1.0,
            ),
        ]
        fit = evidence_fit("H1", PlaybackSource.imported_reconstruction, traj, targets)
        assert fit.residuals[0].distance_m == 1.0 and fit.residuals[0].within_uncertainty
        assert fit.residuals[0].heading_error_deg == 5.0
        assert fit.residuals[1].distance_m is None
        assert fit.rms_residual_m == 1.0
        assert fit.targets_within_uncertainty == 1 and fit.targets_total == 2


def test_registry_ids_unique_and_documented():
    assert len(registry.METRICS) > 50
    for m in registry.METRICS.values():
        assert m.definition and m.calculation and m.missing_value_rule and m.source_outputs
        assert not any(word in m.id for word in ("risk", "safety_score", "efficiency"))
