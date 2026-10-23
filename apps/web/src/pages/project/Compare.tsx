import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, getToken } from "../../api/client";
import { useComparisons, useMetricDefs, useRuns, useScenarios } from "../../api/hooks";
import type { Comparison, Report, Run } from "../../api/types";
import { RunCharts } from "../../components/results";
import { Card, Empty, ErrorNote, Field, PageHead, StatusBadge, fmtDate, fmtNum } from "../../components/ui";
import { useProjectId } from "./ProjectLayout";

export function Compare() {
  const pid = useProjectId();
  const comps = useComparisons(pid);
  const [sel, setSel] = useState<string | null>(null);
  const current = comps.data?.find((c) => c.id === sel) ?? comps.data?.[0];
  return (
    <div className="page">
      <PageHead title="Compare" intro="Comparisons check that runs differ only in the scenario changes: engine and adapter versions, horizon, initial conditions, population or demand, seeds and boundary conditions. Differences that prevent a controlled comparison are flagged." />
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 320px) minmax(0, 1fr)", alignItems: "start" }}>
        <div className="stack">
          <NewComparison onCreated={setSel} />
          <Card title="Saved comparisons">
            {!comps.data?.length && <p className="muted">None yet.</p>}
            <div className="stack">
              {comps.data?.map((c) => (
                <button key={c.id} className="list-item" style={{ textAlign: "left", font: "inherit" }} aria-pressed={current?.id === c.id} onClick={() => setSel(c.id)}>
                  <div style={{ flex: 1 }}>
                    <strong>{c.name}</strong>
                    <div className="muted small">{c.run_ids.length} runs · {fmtDate(c.created_at)}</div>
                  </div>
                  <StatusBadge status={c.compatibility.controlled ? "ok" : "warning"} label={c.compatibility.controlled ? "Controlled" : "Check flags"} />
                </button>
              ))}
            </div>
          </Card>
        </div>
        <div>{current ? <ComparisonView c={current} /> : <Empty>Create a comparison from completed runs.</Empty>}</div>
      </div>
    </div>
  );
}

function NewComparison({ onCreated }: { onCreated: (id: string) => void }) {
  const pid = useProjectId();
  const qc = useQueryClient();
  const runs = useRuns(pid);
  const scenarios = useScenarios(pid);
  const sname = new Map((scenarios.data ?? []).map((s) => [s.id, s.name]));
  const done = (runs.data?.items ?? []).filter((r) => r.state === "completed");
  const [base, setBase] = useState("");
  const [others, setOthers] = useState<string[]>([]);
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<Comparison>(`/projects/${pid}/comparisons`, { name: name || "Comparison", baseline_run_id: base, run_ids: others }),
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ["comparisons", pid] }); onCreated(c.id); setOthers([]); },
  });
  const label = (r: Run) => `${sname.get(r.scenario_id) ?? r.engine_id} · ${r.label ?? r.id.slice(0, 8)}`;
  return (
    <Card title="New comparison">
      <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <Field label="Name" htmlFor="cmp-name"><input id="cmp-name" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Baseline run" htmlFor="cmp-base">
          <select id="cmp-base" required value={base} onChange={(e) => setBase(e.target.value)}>
            <option value="">Select…</option>
            {done.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}
          </select>
        </Field>
        <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="label" style={{ fontWeight: 560, fontSize: 13 }}>Compare with</legend>
          {done.filter((r) => r.id !== base).map((r) => (
            <label key={r.id} className="row small">
              <input type="checkbox" checked={others.includes(r.id)} onChange={(e) => setOthers(e.target.checked ? [...others, r.id] : others.filter((x) => x !== r.id))} />
              {label(r)}
            </label>
          ))}
        </fieldset>
        <ErrorNote error={create.error} />
        <button className="btn btn-primary" disabled={!base || !others.length || create.isPending}>Compare</button>
      </form>
    </Card>
  );
}

function ComparisonView({ c }: { c: Comparison }) {
  const pid = useProjectId();
  const nav = useNavigate();
  const defs = useMetricDefs();
  const runs = useRuns(pid);
  const names = useMemo(() => new Map((defs.data ?? []).map((d) => [d.id, d.name])), [defs.data]);
  const cands = Object.keys(c.table[0]?.candidates ?? {});
  const ordered = c.run_ids.map((id) => runs.data?.items.find((r) => r.id === id)).filter(Boolean) as Run[];
  const labels = [c.compatibility.runs?.[0]?.label ?? "Baseline", ...cands];
  const report = useMutation({
    mutationFn: () => api.post<Report>(`/projects/${pid}/reports`, { kind: "comparison", title: c.name, comparison_id: c.id }),
    onSuccess: () => nav(`/p/${pid}/reports`),
  });
  const csv = async () => {
    const res = await fetch(`/api/v1/comparisons/${c.id}/csv`, { headers: { Authorization: `Bearer ${getToken()}` } });
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `comparison-${c.id}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="stack">
      <Card title={c.name} actions={<><button className="btn btn-small" onClick={csv}>Export CSV</button><button className="btn btn-small btn-primary" onClick={() => report.mutate()} disabled={report.isPending}>Create report</button></>}>
        <div className={`note ${c.compatibility.controlled ? "" : "warn"}`}>{c.compatibility.summary}</div>
        <ErrorNote error={report.error} />
        <details style={{ marginTop: 10 }} open={!c.compatibility.controlled}>
          <summary>Compatibility checks ({c.compatibility.checks.length})</summary>
          <table className="data">
            <thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>{c.compatibility.checks.map((k, i) => <tr key={i}><td>{k.check}</td><td><StatusBadge status={k.status} /></td><td className="small">{k.detail}</td></tr>)}</tbody>
          </table>
        </details>
      </Card>
      <Card title="Differences" intro="Absolute and percentage differences against the baseline. Percentages are omitted where they are not meaningful (zero baseline, unavailable values).">
        {Array.from(new Set(c.table.flatMap((row) => Object.values(row.candidates).map((x) => x.diff_note))
          .filter((n): n is string => !!n && n.startsWith("Lower mean travel time")))).map((n) => <div key={n} className="note warn" style={{ marginBottom: 10 }}>{n}</div>)}
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Metric</th><th>Unit</th><th className="num">Baseline</th>
                {cands.map((n) => <th key={n} className="num" colSpan={2}>{n}</th>)}
              </tr>
            </thead>
            <tbody>
              {c.table.map((row) => (
                <tr key={`${row.metric_id}|${row.entity}`}>
                  <td>{names.get(row.metric_id) ?? row.metric_id}{row.entity ? ` [${row.entity}]` : ""}<div className="muted mono">{row.metric_id}</div></td>
                  <td>{row.unit === "1" ? "ratio" : row.unit}</td>
                  <td className="num">{row.baseline.status === "available" ? fmtNum(row.baseline.value) : <StatusBadge status={row.baseline.status} />}</td>
                  {cands.map((n) => {
                    const x = row.candidates[n];
                    return [
                      <td key={`${n}v`} className="num">{x.status === "available" ? fmtNum(x.value) : <StatusBadge status={x.status} />}</td>,
                      <td key={`${n}d`} className="num small">
                        {x.abs_diff != null ? `${x.abs_diff > 0 ? "+" : ""}${fmtNum(x.abs_diff)}` : "—"}
                        {x.pct_diff != null && <div className="muted">{x.pct_diff > 0 ? "+" : ""}{x.pct_diff.toFixed(1)}%</div>}
                        {x.diff_note && <DiffNote note={x.diff_note} />}
                      </td>,
                    ];
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      {ordered.length > 1 && <Card title="Charts"><RunCharts runs={ordered.slice(0, 4)} names={labels.slice(0, 4)} /></Card>}
    </div>
  );
}

/** Short in-cell form of a comparison note; the full text is the tooltip and, for warnings, listed above the table. */
function DiffNote({ note }: { note: string }) {
  if (note.startsWith("Lower mean travel time")) return <div title={note}><StatusBadge status="warning" label="Not an improvement" /></div>;
  if (note.startsWith("Percentage")) return <div className="muted" title={note}>% not shown</div>;
  return <div className="muted" title={note}>{note.split(":")[0]}</div>;
}
