/** Shared result views: metric tables with definitions, and discipline charts built from normalised series. */
import { useMemo } from "react";
import { useMetricDefs, useRunData } from "../api/hooks";
import type { Metric, Run } from "../api/types";
import { BarChart, LineChart, type LineSeries } from "./charts";
import { StatusBadge, fmtNum } from "./ui";

export function MetricTable({ metrics }: { metrics: Metric[] }) {
  const defs = useMetricDefs();
  const byId = useMemo(() => new Map((defs.data ?? []).map((d) => [d.id, d])), [defs.data]);
  const rows = metrics.filter((m) => !m.entity).concat(metrics.filter((m) => m.entity));
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr><th>Metric</th><th>Entity</th><th className="num">Value</th><th>Unit</th><th>Status</th><th>Note</th></tr>
        </thead>
        <tbody>
          {rows.map((m) => {
            const d = byId.get(m.metric_id);
            return (
              <tr key={`${m.metric_id}|${m.entity}`}>
                <td>
                  <span title={d ? `${d.definition} Calculation: ${d.calculation}` : undefined}>{d?.name ?? m.metric_id}</span>
                  <div className="muted mono">{m.metric_id}</div>
                </td>
                <td>{m.entity ?? "—"}</td>
                <td className="num">
                  {m.status === "available" ? fmtNum(m.value) : "—"}
                  {m.stats && m.stats.sd !== undefined && <div className="muted small">± {fmtNum(m.stats.sd, 3)} sd over replicates</div>}
                </td>
                <td>{m.unit === "1" ? "ratio" : m.unit}</td>
                <td>{m.status === "available" ? <span className="muted small">available</span> : <StatusBadge status={m.status} />}</td>
                <td className="small muted">{m.note ?? (m.status !== "available" ? d?.missing_value_rule : "")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

type Series = Record<string, unknown>;

/** Charts for one or more runs of the same discipline (curves are overlaid, never on a second axis). */
export function RunCharts({ runs, names }: { runs: Run[]; names: string[] }) {
  return <RunChartsInner runs={runs} names={names} />;
}

function RunChartsInner({ runs, names }: { runs: Run[]; names: string[] }) {
  const queries = [useRunData<Series>(runs[0]?.id, "series.json"), useRunData<Series>(runs[1]?.id, "series.json", runs.length > 1),
    useRunData<Series>(runs[2]?.id, "series.json", runs.length > 2), useRunData<Series>(runs[3]?.id, "series.json", runs.length > 3)];
  const data = queries.slice(0, runs.length).map((q) => q.data);
  if (data.every((d) => !d)) return null;
  const evac: LineSeries[] = [];
  const flood: LineSeries[] = [];
  const rain: LineSeries[] = [];
  data.forEach((d, i) => {
    if (!d) return;
    const ev = d.evacuated_over_time as { t_s: number; evacuated: number }[] | undefined;
    if (ev) {
      let last = ev.length - 1;
      while (last > 0 && ev[last - 1].evacuated === ev[ev.length - 1].evacuated) last--;
      evac.push({ name: names[i], points: ev.slice(0, Math.min(ev.length, last + 3)).map((r) => [r.t_s, r.evacuated]) });
    }
    const sys = d.system as { t_s: number; flooding_cms: number; rainfall_mm_h: number }[] | undefined;
    if (sys) {
      flood.push({ name: names[i], points: sys.map((r) => [r.t_s / 3600, r.flooding_cms]) });
      if (i === 0) rain.push({ name: "Rainfall", points: sys.map((r) => [r.t_s / 3600, r.rainfall_mm_h]) });
    }
  });
  const single = runs.length === 1 ? data[0] : undefined;
  const queues = single?.queue_profiles as Record<string, [number, number][]> | undefined;
  const hist = single?.travel_time_histogram as { from_s: number; to_s: number; count: number }[] | undefined;
  return (
    <div className="stack" style={{ gap: 18 }}>
      {evac.length > 0 && <LineChart title="People evacuated over time" series={evac} xLabel="Time since alarm" xUnit="s" yLabel="People evacuated" />}
      {hist && hist.length > 0 && (
        <BarChart title="Travel-time distribution" valueLabel="People" data={hist.map((b) => ({ label: `${fmtNum(b.from_s)}–${fmtNum(b.to_s)} s`, value: b.count }))} />
      )}
      {flood.length > 0 && <LineChart title="System flooding rate" series={flood} xLabel="Time since start" xUnit="h" yLabel="Flooding (m³/s)" yUnit="m³/s" />}
      {rain.length > 0 && runs.length === 1 && <LineChart title="Rainfall intensity" series={rain} xLabel="Time since start" xUnit="h" yLabel="Rainfall (mm/h)" yUnit="mm/h" />}
      {queues && (
        <LineChart title="Queue length over time (replication 0)" xLabel="Time" xUnit="s" yLabel="Waiting requests"
          series={Object.entries(queues).map(([k, pts]) => ({ name: k, points: pts }))} />
      )}
    </div>
  );
}
