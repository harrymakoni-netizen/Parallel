"""Results worker units: chart scales, table twins, brand asset integrity."""

from __future__ import annotations

import hashlib
from pathlib import Path

from parallel_results.charts import Series, compact, line_chart, nice_ticks, sample
from parallel_results.pdf import LOGO, humanise, num

REPO = Path(__file__).resolve().parents[3]


def test_nice_ticks_cover_data_with_round_steps():
    assert nice_ticks(0, 56.27) == [0, 20, 40, 60]
    t = nice_ticks(0, 0.0123)
    assert t[0] == 0 and t[-1] >= 0.0123 and len(t) <= 7
    assert nice_ticks(5, 5)[0] <= 5 <= nice_ticks(5, 5)[-1]


def test_compact_labels():
    assert [compact(v) for v in (0, 2500, 1000, 3_000_000, 0.25, 12)] == [
        "0",
        "2.5K",
        "1K",
        "3M",
        "0.25",
        "12",
    ]


def test_sample_is_step_hold_and_line_chart_caps_series():
    s = Series("a", [(0, 0), (10, 5), (20, 9)])
    at, rows = sample([s], columns=3)
    assert at == [0, 10, 20] and rows == [[0, 5, 9]]
    many = [Series(str(i), [(0, i), (1, i + 1)]) for i in range(6)]
    d = line_chart(many, x_label="x", y_label="y")
    assert d.width == 480  # renders without error; at most four series are drawn


def test_number_and_key_formatting():
    assert num(1204.3) == "1,204" and num(None) == "—" and num(25.2) == "25.2" and num(7) == "7"
    assert humanise("horizon_s") == "Horizon (s)" and humanise("population_positions_sha256").endswith(
        "SHA-256"
    )


def test_packaged_logo_is_the_unaltered_supplied_file():
    canonical = REPO / "brand" / "parallel-logo.webp"
    assert hashlib.sha256(LOGO.read_bytes()).digest() == hashlib.sha256(canonical.read_bytes()).digest()
