import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, sameOrigin, streamEvents } from "../../api/client";
import { useArtifacts, useMetrics, useRun, useRunData, useScenarioRevision } from "../../api/hooks";
import type { Run } from "../../api/types";
import { MetricTable, RunCharts } from "../../components/results";
import { Card, ErrorNote, Field, KV, Loading, PageHead, StatusBadge, fmtBytes, fmtDate, shortId } from "../../components/ui";
import { useProjectId } from "./ProjectLayout";

const TERMINAL = ["completed", "failed", "cancelled"];

export function RunDetail() {
  const pid = useProjectId();
  const { rid } = useParams<{ rid: string }>();
  const qc = useQueryClient();
  const run = useRun(rid);
  const r = run.data;
  const [live, setLive] = useState<{ state: string; progress: number | null; message: string | null } | null>(null);

  useEffect(() => {
    if (!r || TERMINAL.includes(r.state)) return;
    const ctl = new AbortController();
    streamEvents(`/runs/${r.id}/events`, (e) => {
      const d = e.data as { state: string; progress: number | null; message: string | null };
      setLive(d);
      if (TERMINAL.includes(d.state) || d.state === "awaiting_external_result") {
        qc.invalidateQueries({ queryKey: ["run", r.id] });
        qc.invalidateQueries({ queryKey: ["metrics", r.id] });
        qc.invalidateQueries({ queryKey: ["runs", pid] });
      }
    }, ctl.signal).catch(() => undefined);
    return () => ctl.abort();
  }, [r?.id, r?.state, qc, pid]); // eslint-disable-line react-hooks/exhaustive-deps

  const cancel = useMutation({
    mutationFn: () => api.post<Run>(`/runs/${rid}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run", rid] }),
  });
  if (run.isLoading) return <div className="page"><Loading /></div>;
  if (!r) return <div className="page"><ErrorNote error={run.error ?? "Run not found"} /></div>;
  const state = live?.state ?? r.state;
  return (
    <div className="page">
      <PageHead title={r.label ?? `Run ${shortId(r.id)}`} intro={<>Run <span className="mono">{r.id}</span></>}>
        <StatusBadge status={state} />
        {!TERMINAL.includes(state) && state !== "awaiting_external_result" && (
          <button className="btn" onClick={() => cancel.mutate()} disabled={cancel.isPending}>Cancel run</button>
        )}
        {r.state === "completed" && <Link className="btn" to={`../scene?run=${r.id}`}>Play back in scene</Link>}
      </PageHead>
      {!TERMINAL.includes(state) && state !== "awaiting_external_result" && (
        <Card title="Progress">
          <div className="progress" role="progressbar" aria-label="Run progress" aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={Math.round((live?.progress ?? r.progress ?? 0) * 100)}>
            <span style={{ width: `${Math.round((live?.progress ?? r.progress ?? 0) * 100)}%` }} />
          </div>
          <p className="muted small" style={{ marginTop: 6 }} aria-live="polite">{live?.message ?? r.progress_message ?? "Waiting for a worker"}</p>
        </Card>
      )}
      {r.state === "failed" && <ErrorNote error={`${r.error_code ?? "failed"}: ${r.error_message ?? ""}`} />}
      {r.state === "awaiting_external_result" && <ExternalExchange run={r} />}
      <div className="grid grid-2" style={{ marginTop: 14 }}>
        <Card title="Provenance">
          <KV items={[
            ["Engine", `${r.engine_id} ${r.engine_version ?? ""}`],
            ["Adapter", r.adapter_version],
            ["Execution mode", r.execution_mode.replace("_", " ")],
            ["Seed", r.seed ?? "none"],
            ["Scenario revision", <ScenarioRevLink key="s" id={r.scenario_revision_id} />],
            ["Model revision", <span key="m" className="mono">{r.domain_model_revision_id}</span>],
            ["Scene revision", r.scene_revision_id ? <span className="mono">{r.scene_revision_id}</span> : "none"],
            ["Attempts", r.attempts],
            ["Started / finished", `${fmtDate(r.started_at)} → ${fmtDate(r.finished_at)}`],
            ...(r.external_installation ? [["Imported from", r.external_installation] as [string, string]] : []),
          ]} />
          <details style={{ marginTop: 8 }}>
            <summary>Input hashes ({Object.keys(r.input_hashes).length})</summary>
            <table className="data"><tbody>{Object.entries(r.input_hashes).map(([k, v]) => <tr key={k}><td>{k}</td><td className="mono">{v}</td></tr>)}</tbody></table>
          </details>
        </Card>
        <Card title="Validation, model changes and warnings">
          {r.model_change_notes.length > 0 && (
            <>
              <h3>How the scenario changed the model</h3>
              <ul>{r.model_change_notes.map((n) => <li key={n}>{n}</li>)}</ul>
            </>
          )}
          {(r.validation.issues ?? []).length > 0 && (
            <ul>{r.validation.issues!.map((i, k) => <li key={k}><StatusBadge status={i.severity} /> {i.message}</li>)}</ul>
          )}
          {r.warnings.length ? <ul>{r.warnings.map((w) => <li key={w}><StatusBadge status="warning" label="Warning" /> {w}</li>)}</ul>
            : <p className="muted">No warnings recorded.</p>}
          <Crosschecks run={r} />
        </Card>
      </div>
      {r.state === "completed" && <Results run={r} />}
      <StateHistory run={r} />
    </div>
  );
}

function ScenarioRevLink({ id }: { id: string }) {
  const rev = useScenarioRevision(id);
  return <span>{rev.data ? `revision ${rev.data.number} (${rev.data.status})` : ""} <span className="mono">{shortId(id)}</span></span>;
}

function Crosschecks({ run }: { run: Run }) {
  const checks = (run.result_summary.crosschecks as { name: string; passed: boolean; detail?: string; normalised?: number; native?: number }[] | undefined) ?? [];
  if (!checks.length) return null;
  return (
    <>
      <h3 style={{ marginTop: 10 }}>Native cross-checks</h3>
      <ul>{checks.map((c) => <li key={c.name}><StatusBadge status={c.passed ? "ok" : "failed"} label={c.passed ? "Passed" : "Failed"} /> {c.name}</li>)}</ul>
    </>
  );
}

function Results({ run }: { run: Run }) {
  const metrics = useMetrics(run.id);
  const arts = useArtifacts(run.id);
  const field = useRunData<{ x: number[]; y: number[]; speed: number[]; cell_size_m: number; note: string }>(run.id, "field.json", run.engine_id === "openfoam");
  const download = async (id: string) => {
    const u = await api.get<{ url: string }>(`/runs/${run.id}/artifacts/${id}/url`);
    window.location.assign(sameOrigin(u.url));
  };
  return (
    <>
      <Card title="Results" intro="Recorded metrics with their identifiers. Unattained or unavailable values are shown as such, never estimated.">
        {metrics.data ? <MetricTable metrics={metrics.data} /> : <Loading />}
      </Card>
      <Card title="Charts"><RunCharts runs={[run]} names={["This run"]} /></Card>
      {field.data && <FieldPlot field={field.data} />}
      <Card title="Artifacts" intro="Inputs, raw engine outputs, logs and normalised results, each with its SHA-256 hash.">
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Name</th><th>Kind</th><th className="num">Size</th><th>SHA-256</th><th /></tr></thead>
            <tbody>
              {arts.data?.map((a) => (
                <tr key={a.id}>
                  <td>{a.name}<div className="muted small">{a.description}</div></td>
                  <td>{a.kind.replace("_", " ")}</td>
                  <td className="num">{fmtBytes(a.size_bytes)}</td>
                  <td className="mono">{a.sha256.slice(0, 16)}…</td>
                  <td><button className="btn btn-small" onClick={() => download(a.id)}>Download</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

/** Plan-view speed field (sequential one-hue ramp, with a scale legend). */
function FieldPlot({ field }: { field: { x: number[]; y: number[]; speed: number[]; cell_size_m: number; note: string } }) {
  const ramp = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
  const xs = field.x, ys = field.y, sp = field.speed;
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const vmax = Math.max(...sp) || 1;
  const W = 640, H = Math.round(W * ((y1 - y0 + field.cell_size_m) / (x1 - x0 + field.cell_size_m)));
  const cell = (W / (x1 - x0 + field.cell_size_m)) * field.cell_size_m;
  return (
    <Card title="Wind speed field" intro={field.note}>
      <svg width="100%" viewBox={`0 0 ${W} ${H + 40}`} role="img" aria-label="Plan-view wind speed field; values are in the run's field artifact">
        {xs.map((x, i) => (
          <rect key={i} x={((x - x0) / (x1 - x0 + field.cell_size_m)) * W} y={H - ((ys[i] - y0) / (y1 - y0 + field.cell_size_m)) * H - cell}
            width={cell + 0.5} height={cell + 0.5} fill={ramp[Math.min(ramp.length - 1, Math.floor((sp[i] / vmax) * ramp.length))]} />
        ))}
        {ramp.map((c, i) => <rect key={c} x={20 + i * 40} y={H + 12} width={38} height={10} fill={c} />)}
        <text x={20} y={H + 36} fontSize={11} fill="var(--chart-muted)">0 m/s</text>
        <text x={20 + ramp.length * 40} y={H + 36} fontSize={11} fill="var(--chart-muted)" textAnchor="end">{vmax.toFixed(1)} m/s</text>
      </svg>
    </Card>
  );
}

function StateHistory({ run }: { run: Run }) {
  return (
    <Card title="State history">
      <ol className="small" style={{ margin: 0, paddingLeft: 18 }}>
        {run.state_history.map((h, i) => (
          <li key={i}><strong>{h.state.replace(/_/g, " ")}</strong> <span className="muted">{fmtDate(h.at)}{h.message ? ` — ${h.message}` : ""}</span></li>
        ))}
      </ol>
    </Card>
  );
}

function ExternalExchange({ run }: { run: Run }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [inst, setInst] = useState("");
  const [prov, setProv] = useState("");
  const [options, setOptions] = useState("{}");
  const pkg = useMutation({
    mutationFn: async () => {
      const u = await api.get<{ url: string }>(`/runs/${run.id}/exchange-package`);
      window.location.assign(sameOrigin(u.url));
    },
  });
  const upload = useMutation({
    mutationFn: () => {
      const f = new FormData();
      f.append("file", file!);
      f.append("external_installation", inst);
      f.append("supplied_provenance", prov);
      f.append("options", options);
      return api.upload<Run>(`/runs/${run.id}/external-result`, f);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run", run.id] }),
  });
  return (
    <Card title="Awaiting an external result" intro="This engine runs on a licensed installation outside Parallel. Download the exchange package, run it there, then upload the result package. This is file exchange, not automated execution.">
      <div className="stack">
        <div><button className="btn" onClick={() => pkg.mutate()}>Download exchange package</button></div>
        <form className="stack" onSubmit={(e) => { e.preventDefault(); upload.mutate(); }}>
          <Field label="Result package (.zip)" htmlFor="ex-file"><input id="ex-file" type="file" accept=".zip" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>
          <Field label="Installation that produced it" htmlFor="ex-inst"><input id="ex-inst" required minLength={3} value={inst} onChange={(e) => setInst(e.target.value)} /></Field>
          <Field label="How it was produced" htmlFor="ex-prov" hint="Operator's account: who ran it, when, with which settings."><textarea id="ex-prov" required minLength={3} value={prov} onChange={(e) => setProv(e.target.value)} /></Field>
          <Field label="Import options (JSON)" htmlFor="ex-opt" hint="For example the confirmed column mapping of an exported trajectory table."><textarea id="ex-opt" className="mono" value={options} onChange={(e) => setOptions(e.target.value)} /></Field>
          <ErrorNote error={upload.error ?? pkg.error} />
          <div><button className="btn btn-primary" type="submit" disabled={!file || upload.isPending}>{upload.isPending ? "Importing…" : "Import result"}</button></div>
        </form>
      </div>
    </Card>
  );
}
