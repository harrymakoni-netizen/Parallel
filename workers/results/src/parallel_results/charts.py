"""Report charts drawn with reportlab graphics.

Follows the house chart specification in print (light) mode: categorical
slots in fixed order (validated with the palette validator: slots 1-3 pass
all-pairs on white, aqua needs visible labels or a table, so every chart is
paired with a table in the PDF), 1.5 pt (2 px) lines with round joins, solid
hairline gridlines one step off the surface, end markers with a surface ring,
a legend whenever there are two or more series, direct end labels only when
they do not collide, and all text in ink colours rather than series colours.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, String
from reportlab.lib.colors import HexColor

SURFACE = HexColor("#ffffff")
INK = HexColor("#0b0b0b")
INK_2 = HexColor("#52514e")
MUTED = HexColor("#898781")
GRID = HexColor("#e1e0d9")
AXIS = HexColor("#c3c2b7")
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")  # light slots 1-4; never cycled
MAX_SERIES = len(SERIES)
FONT = "Helvetica"


@dataclass
class Series:
    name: str
    points: list[tuple[float, float]]


def nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    if not math.isfinite(lo) or not math.isfinite(hi):
        return [0.0, 1.0]
    if hi <= lo:
        hi = lo + (abs(lo) or 1.0)
    span = hi - lo
    mag = 10 ** math.floor(math.log10(span / target))
    step = mag
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if span / step <= target:
            break
    start = math.floor(lo / step + 1e-9) * step
    ticks, v = [], start
    while v < hi + step * 0.999:
        ticks.append(round(v, 10))
        v += step
    return ticks


def compact(v: float) -> str:
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:g}M"
    if a >= 1e3:
        return f"{v / 1e3:g}K"
    if a == 0:
        return "0"
    return f"{v:g}" if a >= 1 else f"{v:.3g}"


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def line_chart(
    series: Sequence[Series], *, x_label: str, y_label: str, width: float = 480, height: float = 210
) -> Drawing:
    """Multi-series line chart on one y-scale (never a second axis)."""
    series = [s for s in series if s.points][:MAX_SERIES]
    d = Drawing(width, height)
    legend_h = 0.0
    if len(series) >= 2:  # legend row(s); a single series is named by the chart title
        x, row = 0.0, 0
        for i, s in enumerate(series):
            label = _truncate(s.name, 42)
            w = 18 + len(label) * 4.1 + 14
            if x + w > width and x > 0:
                x, row = 0.0, row + 1
            y = height - 8 - row * 12
            d.add(
                Line(
                    x,
                    y + 2.5,
                    x + 12,
                    y + 2.5,
                    strokeColor=HexColor(SERIES[i]),
                    strokeWidth=1.5,
                    strokeLineCap=1,
                )
            )
            d.add(String(x + 16, y, label, fontName=FONT, fontSize=7.5, fillColor=INK_2))
            x += w
        legend_h = (row + 1) * 12 + 4
    xs = [p[0] for s in series for p in s.points]
    ys = [p[1] for s in series for p in s.points]
    xt = nice_ticks(min(xs), max(xs))
    yt = nice_ticks(min(0.0, min(ys)), max(ys) if max(ys) > 0 else 1.0)
    left, bottom = 38.0, 30.0
    top = height - legend_h - 16  # room for the y-axis caption
    x0, x1, y0, y1 = xt[0], xt[-1], yt[0], yt[-1]

    def sy(v: float) -> float:
        return bottom + (v - y0) / (y1 - y0) * (top - bottom)

    # direct end labels only if they separate cleanly at the right edge
    ends = sorted((sy(s.points[-1][1]), i) for i, s in enumerate(series))
    direct = len(series) >= 2 and all(b[0] - a[0] >= 9 for a, b in zip(ends, ends[1:], strict=False))
    right = width - (92 if direct else 10)

    def sx(v: float) -> float:
        return left + (v - x0) / (x1 - x0) * (right - left)

    d.add(String(left - 34, top + 6, y_label, fontName=FONT, fontSize=7.5, fillColor=INK_2))
    for v in yt:
        y = sy(v)
        color, w = (AXIS, 0.75) if v == 0 or v == y0 else (GRID, 0.5)
        d.add(Line(left, y, right, y, strokeColor=color, strokeWidth=w))
        d.add(
            String(
                left - 4, y - 2.5, compact(v), fontName=FONT, fontSize=7, fillColor=MUTED, textAnchor="end"
            )
        )
    for v in xt:
        d.add(
            String(
                sx(v),
                bottom - 11,
                compact(v),
                fontName=FONT,
                fontSize=7,
                fillColor=MUTED,
                textAnchor="middle",
            )
        )
    d.add(
        String(
            (left + right) / 2,
            bottom - 23,
            x_label,
            fontName=FONT,
            fontSize=7.5,
            fillColor=INK_2,
            textAnchor="middle",
        )
    )
    for i, s in enumerate(series):
        color = HexColor(SERIES[i])
        pts: list[float] = []
        for px, py in s.points:
            pts += [sx(px), sy(py)]
        d.add(PolyLine(pts, strokeColor=color, strokeWidth=1.5, strokeLineCap=1, strokeLineJoin=1))
        ex, ey = pts[-2], pts[-1]
        d.add(Circle(ex, ey, 3, fillColor=color, strokeColor=SURFACE, strokeWidth=1.5))
        if direct:
            d.add(String(ex + 7, ey - 2.5, _truncate(s.name, 22), fontName=FONT, fontSize=7, fillColor=INK_2))
    return d


def sample(series: Sequence[Series], columns: int = 7) -> tuple[list[float], list[list[float | None]]]:
    """Values of each series at evenly spaced x positions (the chart's table twin)."""
    xs = [p[0] for s in series for p in s.points]
    if not xs:
        return [], []
    lo, hi = min(xs), max(xs)
    at = [lo + (hi - lo) * k / (columns - 1) for k in range(columns)]
    rows = []
    for s in series:
        row: list[float | None] = []
        for x in at:
            prior = [p for p in s.points if p[0] <= x + 1e-9]
            row.append(prior[-1][1] if prior else None)
        rows.append(row)
    return at, rows
