/**
 * Charts following the house chart specification: validated categorical slots in fixed order
 * (never cycled, max 4), 2px lines with round joins, solid hairline gridlines, end markers with a
 * 2px surface ring, a legend for two or more series, direct end labels only when they do not collide,
 * text in ink colours, a crosshair tooltip listing every series, keyboard access, and a table twin.
 */
import { useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { fmtNum } from "./ui";

export const SERIES_VARS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"];
export const MAX_SERIES = SERIES_VARS.length;

export interface LineSeries {
  name: string;
  points: [number, number][];
}

export function niceTicks(lo: number, hi: number, target = 5): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  if (hi <= lo) hi = lo + (Math.abs(lo) || 1);
  const span = hi - lo;
  const mag = 10 ** Math.floor(Math.log10(span / target));
  let step = mag;
  for (const m of [1, 2, 2.5, 5, 10]) {
    step = m * mag;
    if (span / step <= target) break;
  }
  const out: number[] = [];
  for (let v = Math.floor(lo / step + 1e-9) * step; v < hi + step * 0.999; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

export function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `${Number((v / 1e6).toPrecision(3))}M`;
  if (a >= 1e3) return `${Number((v / 1e3).toPrecision(3))}K`;
  if (a === 0) return "0";
  return a >= 1 ? `${Number(v.toPrecision(4))}` : `${Number(v.toPrecision(3))}`;
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(640);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((e) => setW(Math.max(280, e[0].contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function stepValue(points: [number, number][], x: number): number | null {
  let v: number | null = null;
  for (const [px, py] of points) {
    if (px <= x + 1e-9) v = py;
    else break;
  }
  return v;
}

export function LineChart({ series, xLabel, yLabel, height = 260, title, xUnit = "", yUnit = "" }: {
  series: LineSeries[]; xLabel: string; yLabel: string; height?: number; title: string; xUnit?: string; yUnit?: string;
}) {
  const shown = series.filter((s) => s.points.length).slice(0, MAX_SERIES);
  const [wrap, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const titleId = useId();
  const geo = useMemo(() => {
    const xs = shown.flatMap((s) => s.points.map((p) => p[0]));
    const ys = shown.flatMap((s) => s.points.map((p) => p[1]));
    if (!xs.length) return null;
    const xt = niceTicks(Math.min(...xs), Math.max(...xs));
    const yt = niceTicks(Math.min(0, ...ys), Math.max(...ys) > 0 ? Math.max(...ys) : 1);
    const legendH = shown.length >= 2 ? 22 : 0;
    const top = legendH + 22;
    const bottom = height - 34;
    const left = 46;
    const sy = (v: number) => bottom - ((v - yt[0]) / (yt[yt.length - 1] - yt[0])) * (bottom - top);
    const ends = shown.map((s, i) => [sy(s.points[s.points.length - 1][1]), i] as [number, number]).sort((a, b) => a[0] - b[0]);
    const direct = shown.length >= 2 && ends.every((e, i) => i === 0 || e[0] - ends[i - 1][0] >= 13);
    const right = width - (direct ? 120 : 12);
    const sx = (v: number) => left + ((v - xt[0]) / (xt[xt.length - 1] - xt[0])) * (right - left);
    const grid = Array.from(new Set(xs)).sort((a, b) => a - b);
    return { xt, yt, top, bottom, left, right, sx, sy, direct, grid, legendH };
  }, [shown, width, height]);

  if (!geo) return <p className="muted">No data for {title.toLowerCase()}.</p>;
  const { xt, yt, top, bottom, left, right, sx, sy, direct, grid } = geo;
  const hx = hover === null ? null : grid[hover];
  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = xt[0] + ((e.clientX - r.left) / r.width) * (xt[xt.length - 1] - xt[0]);
    let best = 0;
    for (let i = 1; i < grid.length; i++) if (Math.abs(grid[i] - x) < Math.abs(grid[best] - x)) best = i;
    setHover(best);
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowRight") setHover((h) => Math.min(grid.length - 1, (h ?? -1) + 1));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? 1) - 1));
    else if (e.key === "Escape") setHover(null);
    else return;
    e.preventDefault();
  };
  return (
    <figure className="chart" aria-labelledby={titleId} style={{ margin: 0 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <figcaption id={titleId} style={{ fontWeight: 600 }}>{title}</figcaption>
        <button className="btn btn-small btn-ghost" onClick={() => setTable((t) => !t)} aria-pressed={table}>
          {table ? "Show chart" : "Show table"}
        </button>
      </div>
      {table ? (
        <ChartTable series={shown} xLabel={xLabel} xUnit={xUnit} yUnit={yUnit} />
      ) : (
        <div ref={wrap} style={{ position: "relative" }}>
          <svg width={width} height={height} role="img" aria-label={`${title}: ${shown.map((s) => s.name).join(", ")}. Use the table view for values.`}
            tabIndex={0} onKeyDown={onKey} onBlur={() => setHover(null)} style={{ display: "block", overflow: "visible" }}>
            {shown.length >= 2 && (
              <g>
                {(() => {
                  let x = 0;
                  return shown.map((s, i) => {
                    const g = (
                      <g key={s.name} transform={`translate(${x},8)`}>
                        <line x1={0} x2={14} y1={0} y2={0} stroke={SERIES_VARS[i]} strokeWidth={2} strokeLinecap="round" />
                        <text x={20} y={4} fontSize={12} fill="var(--text-2)">{s.name}</text>
                      </g>
                    );
                    x += 30 + s.name.length * 6.6;
                    return g;
                  });
                })()}
              </g>
            )}
            <text x={left - 40} y={top - 9} fontSize={12} fill="var(--text-2)">{yLabel}</text>
            {yt.map((v) => (
              <g key={v}>
                <line x1={left} x2={right} y1={sy(v)} y2={sy(v)} stroke={v === yt[0] ? "var(--chart-axis)" : "var(--chart-grid)"} strokeWidth={1} />
                <text x={left - 6} y={sy(v) + 4} fontSize={11} textAnchor="end" fill="var(--chart-muted)" style={{ fontVariantNumeric: "tabular-nums" }}>{compact(v)}</text>
              </g>
            ))}
            {xt.map((v) => (
              <text key={v} x={sx(v)} y={bottom + 16} fontSize={11} textAnchor="middle" fill="var(--chart-muted)">{compact(v)}</text>
            ))}
            <text x={(left + right) / 2} y={bottom + 31} fontSize={12} textAnchor="middle" fill="var(--text-2)">{xLabel}</text>
            {shown.map((s, i) => {
              const d = s.points.map((p, k) => `${k ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("");
              const last = s.points[s.points.length - 1];
              return (
                <g key={s.name}>
                  <path d={d} fill="none" stroke={SERIES_VARS[i]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                  <circle cx={sx(last[0])} cy={sy(last[1])} r={4} fill={SERIES_VARS[i]} stroke="var(--chart-surface)" strokeWidth={2} />
                  {direct && (
                    <text x={sx(last[0]) + 8} y={sy(last[1]) + 4} fontSize={11.5} fill="var(--text-2)">
                      {s.name.length > 18 ? `${s.name.slice(0, 17)}…` : s.name}
                    </text>
                  )}
                </g>
              );
            })}
            {hx !== null && (
              <g pointerEvents="none">
                <line x1={sx(hx)} x2={sx(hx)} y1={top} y2={bottom} stroke="var(--chart-axis)" strokeWidth={1} />
                {shown.map((s, i) => {
                  const v = stepValue(s.points, hx);
                  return v === null ? null : (
                    <circle key={s.name} cx={sx(hx)} cy={sy(v)} r={4} fill={SERIES_VARS[i]} stroke="var(--chart-surface)" strokeWidth={2} />
                  );
                })}
              </g>
            )}
            <rect x={left} y={top} width={Math.max(0, right - left)} height={Math.max(0, bottom - top)} fill="transparent"
              onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
          </svg>
          {hx !== null && (
            <div className="chart-tip" role="status" style={{ left: Math.min(sx(hx) + 12, width - 190), top: top }}>
              <div className="muted small">{xLabel}: {fmtNum(hx)}{xUnit ? ` ${xUnit}` : ""}</div>
              {shown.map((s, i) => (
                <div key={s.name} className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                  <svg width={12} height={4} aria-hidden="true"><line x1={0} x2={12} y1={2} y2={2} stroke={SERIES_VARS[i]} strokeWidth={2} /></svg>
                  <strong>{fmtNum(stepValue(s.points, hx))}{yUnit ? ` ${yUnit}` : ""}</strong>
                  <span className="muted small">{s.name}</span>
                </div>
              ))}
            </div>
          )}
          {series.length > MAX_SERIES && (
            <p className="muted small">Showing the first {MAX_SERIES} of {series.length} series; every value is in the table view.</p>
          )}
        </div>
      )}
    </figure>
  );
}

function ChartTable({ series, xLabel, xUnit, yUnit }: { series: LineSeries[]; xLabel: string; xUnit: string; yUnit: string }) {
  const xs = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p[0])))).sort((a, b) => a - b);
  return (
    <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
      <table className="data">
        <thead>
          <tr>
            <th className="num">{xLabel}{xUnit ? ` (${xUnit})` : ""}</th>
            {series.map((s) => <th key={s.name} className="num">{s.name}{yUnit ? ` (${yUnit})` : ""}</th>)}
          </tr>
        </thead>
        <tbody>
          {xs.map((x) => (
            <tr key={x}>
              <td className="num">{fmtNum(x)}</td>
              {series.map((s) => <td key={s.name} className="num">{fmtNum(stepValue(s.points, x))}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Horizontal bars for one measure across categories (one series = one colour, never a value ramp). */
export function BarChart({ data, title, unit = "", valueLabel }: {
  data: { label: string; value: number | null; note?: string }[]; title: string; unit?: string; valueLabel: string;
}) {
  const [wrap, width] = useWidth<HTMLDivElement>();
  const [table, setTable] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const vals = data.map((d) => d.value ?? 0);
  const max = Math.max(...vals, 0) || 1;
  const labelW = Math.min(220, Math.max(80, ...data.map((d) => d.label.length * 6.8)));
  const barH = 18;
  const gap = 10;
  const h = data.length * (barH + gap) + 8;
  const sx = (v: number) => (v / max) * Math.max(40, width - labelW - 90);
  return (
    <figure style={{ margin: 0 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <figcaption style={{ fontWeight: 600 }}>{title}</figcaption>
        <button className="btn btn-small btn-ghost" onClick={() => setTable((t) => !t)} aria-pressed={table}>
          {table ? "Show chart" : "Show table"}
        </button>
      </div>
      {table ? (
        <table className="data">
          <thead><tr><th>Category</th><th className="num">{valueLabel}{unit ? ` (${unit})` : ""}</th></tr></thead>
          <tbody>{data.map((d) => <tr key={d.label}><td>{d.label}</td><td className="num">{d.value === null ? d.note ?? "unavailable" : fmtNum(d.value)}</td></tr>)}</tbody>
        </table>
      ) : (
        <div ref={wrap}>
          <svg width={width} height={h} role="img" aria-label={`${title}. Use the table view for values.`}>
            {data.map((d, i) => {
              const y = 4 + i * (barH + gap);
              const w = d.value === null ? 0 : Math.max(2, sx(d.value));
              const r = Math.min(4, w / 2);
              return (
                <g key={d.label} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
                  <text x={labelW - 8} y={y + barH / 2 + 4} fontSize={12} textAnchor="end" fill="var(--text-2)">
                    {d.label.length > 30 ? `${d.label.slice(0, 29)}…` : d.label}
                  </text>
                  {d.value !== null && (
                    <path d={`M${labelW},${y} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${barH - 2 * r} a${r},${r} 0 0 1 -${r},${r} h-${w - r} z`}
                      fill="var(--chart-1)" opacity={hover === null || hover === i ? 1 : 0.55} />
                  )}
                  <text x={labelW + w + 6} y={y + barH / 2 + 4} fontSize={12} fill="var(--text-1)" style={{ fontVariantNumeric: "tabular-nums" }}>
                    {d.value === null ? d.note ?? "unavailable" : `${fmtNum(d.value)}${unit ? ` ${unit}` : ""}`}
                  </text>
                  <rect x={0} y={y - gap / 2} width={width} height={barH + gap} fill="transparent" />
                </g>
              );
            })}
          </svg>
        </div>
      )}
    </figure>
  );
}
