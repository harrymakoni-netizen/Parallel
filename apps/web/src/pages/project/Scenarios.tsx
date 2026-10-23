import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useEngines, useMetricDefs, useModelRevision, useModels, useOptimisations, useScenarioRevision, useScenarios } from "../../api/hooks";
import type { DomainModel, Optimisation, Run, Scenario } from "../../api/types";
import { useOrg } from "../../components/Shell";
import { Card, Dialog, Empty, ErrorNote, Field, JsonView, KV, PageHead, ProvenanceBadge, StatusBadge, fmtNum } from "../../components/ui";
import { DISCIPLINES } from "../common";
import { useProjectId } from "./ProjectLayout";

export function Scenarios() {
  const pid = useProjectId();
  const scenarios = useScenarios(pid);
  const models = useModels(pid);
  const [sel, setSel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const current = scenarios.data?.find((s) => s.id === sel) ?? scenarios.data?.[0];
  const byModel = new Map<string, Scenario[]>();
  scenarios.data?.forEach((s) => byModel.set(s.domain_model_id, [...(byModel.get(s.domain_model_id) ?? []), s]));
  return (
    <div className="page">
      <PageHead title="Scenarios" intro="A scenario describes proposed changes, assumptions and operating conditions against a model. AI-drafted scenarios must be adopted by a person before they can run.">
        <button className="btn btn-primary" onClick={() => setCreating(true)} disabled={!models.data?.length}>New scenario</button>
      </PageHead>
      {!scenarios.data?.length && <Empty>No scenarios yet.</Empty>}
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 300px) minmax(0, 1fr)", alignItems: "start" }}>
        <div className="stack">
          {models.data?.map((m) => (
            <div key={m.id}>
              <div className="muted small" style={{ margin: "6px 2px" }}>{m.name}</div>
              <div className="stack" style={{ gap: 6 }}>
                {byModel.get(m.id)?.map((s) => (
                  <button key={s.id} className="list-item" style={{ font: "inherit", textAlign: "left" }} aria-pressed={current?.id === s.id} onClick={() => setSel(s.id)}>
                    <div style={{ flex: 1 }}>
                      <strong>{s.name}</strong>
                      <div className="muted small">{s.engine_id}{s.is_baseline ? " · baseline" : ""}</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
        {current && <ScenarioDetail scenario={current} model={models.data?.find((m) => m.id === current.domain_model_id)} />}
      </div>
      <NewScenario open={creating} onClose={() => setCreating(false)} models={models.data ?? []} />
      <Optimisations />
    </div>
  );
}

function ScenarioDetail({ scenario, model }: { scenario: Scenario; model?: DomainModel }) {
  const pid = useProjectId();
  const qc = useQueryClient();
  const nav = useNavigate();
  const rev = useScenarioRevision(scenario.current_revision_id);
  const [editing, setEditing] = useState(false);
  const [check, setCheck] = useState<{ validation: { ok: boolean; issues: { severity: string; message: string }[] }; resource_estimate: Record<string, unknown> | null } | null>(null);
  const validate = useMutation({ mutationFn: () => api.post<typeof check>(`/scenario-revisions/${rev.data!.id}/validate`), onSuccess: setCheck });
  const adopt = useMutation({ mutationFn: () => api.post(`/scenario-revisions/${rev.data!.id}/adopt`), onSuccess: () => qc.invalidateQueries({ queryKey: ["scenario-rev"] }) });
  const run = useMutation({
    mutationFn: () => api.post<Run>(`/projects/${pid}/runs`, { scenario_revision_id: rev.data!.id }, { headers: { "Idempotency-Key": crypto.randomUUID() } }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["runs", pid] }); nav(`/p/${pid}/runs/${r.id}`); },
  });
  const r = rev.data;
  if (!r) return null;
  const c = r.content as { description?: string; controls: Record<string, unknown>; settings: Record<string, unknown>; assumptions: { id: string; statement: string; rationale?: string }[]; provenance?: { kind: string } };
  const unadopted = r.origin === "ai" && !r.adopted_by;
  return (
    <div className="stack">
      <Card title={scenario.name} intro={`${scenario.engine_id} · revision ${r.number} (${r.status}) · origin ${r.origin}`}
        actions={<>
          <button className="btn btn-small" onClick={() => validate.mutate()}>Validate</button>
          <button className="btn btn-small" onClick={() => setEditing(true)}>Edit</button>
          {unadopted && <button className="btn btn-small" onClick={() => adopt.mutate()}>Adopt draft</button>}
          <button className="btn btn-small btn-primary" onClick={() => run.mutate()} disabled={unadopted || run.isPending}>{run.isPending ? "Submitting…" : "Run"}</button>
        </>}>
        {unadopted && <div className="note warn">Drafted by the assistant. Review it and adopt it before it can run.</div>}
        {c.description && <p style={{ marginBottom: 10 }}>{c.description}</p>}
        <ErrorNote error={run.error ?? adopt.error ?? validate.error} />
        <div className="grid grid-2">
          <div>
            <h3>Controls</h3>
            <KV items={Object.entries(c.controls).filter(([k]) => k !== "kind").map(([k, v]) => [k.replace(/_/g, " "), typeof v === "object" ? <code>{JSON.stringify(v)}</code> : String(v)])} />
          </div>
          <div>
            <h3>Settings</h3>
            <KV items={[["Seed", String(c.settings.seed ?? "none")], ["Timeout", `${c.settings.timeout_s} s`], ["Model", model?.name ?? "—"],
              ["Provenance", c.provenance ? <ProvenanceBadge kind={c.provenance.kind} /> : "—"]]} />
          </div>
        </div>
        {c.assumptions.length > 0 && (
          <>
            <h3 style={{ marginTop: 10 }}>Assumptions</h3>
            <ul>{c.assumptions.map((a) => <li key={a.id}>{a.statement}{a.rationale && <span className="muted"> — {a.rationale}</span>}</li>)}</ul>
          </>
        )}
        {check && (
          <div style={{ marginTop: 10 }}>
            <StatusBadge status={check.validation.ok ? "ok" : "failed"} label={check.validation.ok ? "Valid for this engine" : "Not valid"} />
            <ul>{check.validation.issues.map((i, k) => <li key={k}><StatusBadge status={i.severity} /> {i.message}</li>)}</ul>
            {check.resource_estimate && <p className="small muted">Estimate: {String(check.resource_estimate.basis ?? "")}</p>}
          </div>
        )}
      </Card>
      <EditScenario open={editing} onClose={() => setEditing(false)} scenario={scenario} content={r.content} baseRevision={r.id} model={model} />
    </div>
  );
}

function EditScenario({ open, onClose, scenario, content, baseRevision, model }: {
  open: boolean; onClose: () => void; scenario: Scenario; content: Record<string, unknown>; baseRevision: string; model?: DomainModel;
}) {
  const qc = useQueryClient();
  const [text, setText] = useState(() => JSON.stringify(content, null, 2));
  const mrev = useModelRevision(model?.current_revision_id);
  const ped = model?.discipline === "pedestrian";
  const parsed = useMemo(() => { try { return JSON.parse(text); } catch { return null; } }, [text]);
  const mc = (mrev.data?.content as { content?: { route_segments?: { id: string; name: string }[]; exits?: { id: string; name: string }[] } } | undefined)?.content;
  const toggle = (key: "closed_route_segments" | "closed_exits", id: string, on: boolean) => {
    if (!parsed) return;
    const cur: string[] = parsed.controls[key] ?? [];
    parsed.controls[key] = on ? [...new Set([...cur, id])] : cur.filter((x) => x !== id);
    setText(JSON.stringify(parsed, null, 2));
  };
  const save = useMutation({
    mutationFn: () => api.post(`/scenarios/${scenario.id}/revisions`, { content: JSON.parse(text), base_revision_id: baseRevision }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["scenarios"] }); qc.invalidateQueries({ queryKey: ["scenario-rev"] }); onClose(); },
  });
  return (
    <Dialog open={open} onClose={onClose} title={`Edit ${scenario.name}`}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={() => save.mutate()} disabled={!parsed}>Save revision</button></>}>
      {ped && parsed && mc && (
        <div className="grid grid-2">
          <fieldset style={{ border: 0, padding: 0 }}><legend><strong>Routes unavailable from the start</strong></legend>
            {mc.route_segments?.map((r) => <label key={r.id} className="row small"><input type="checkbox" checked={(parsed.controls.closed_route_segments ?? []).includes(r.id)} onChange={(e) => toggle("closed_route_segments", r.id, e.target.checked)} />{r.name}</label>)}
          </fieldset>
          <fieldset style={{ border: 0, padding: 0 }}><legend><strong>Exits unavailable from the start</strong></legend>
            {mc.exits?.map((r) => <label key={r.id} className="row small"><input type="checkbox" checked={(parsed.controls.closed_exits ?? []).includes(r.id)} onChange={(e) => toggle("closed_exits", r.id, e.target.checked)} />{r.name}</label>)}
          </fieldset>
        </div>
      )}
      <Field label="Scenario content (JSON)" htmlFor="es-json" hint="Validated on save against the scenario contract and the engine adapter.">
        <textarea id="es-json" className="mono" style={{ minHeight: 280 }} value={text} onChange={(e) => setText(e.target.value)} />
      </Field>
      {!parsed && <p className="small" style={{ color: "var(--danger-text)" }}>The JSON is not valid.</p>}
      <ErrorNote error={save.error} />
    </Dialog>
  );
}

function NewScenario({ open, onClose, models }: { open: boolean; onClose: () => void; models: DomainModel[] }) {
  const pid = useProjectId();
  const org = useOrg();
  const qc = useQueryClient();
  const engines = useEngines(org?.id);
  const [modelId, setModelId] = useState("");
  const [name, setName] = useState("");
  const [engine, setEngine] = useState("");
  const [description, setDescription] = useState("");
  const model = models.find((m) => m.id === modelId);
  const options = (engines.data ?? []).filter((e) => model && e.capability.disciplines.includes(model.discipline));
  const create = useMutation({
    mutationFn: () => api.post(`/projects/${pid}/scenarios`, {
      domain_model_id: modelId, name,
      content: { discipline: model!.discipline, engine_id: engine || options[0]?.capability.engine_id, description, controls: { kind: model!.discipline }, settings: { seed: 42, timeout_s: 900 } },
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["scenarios", pid] }); onClose(); },
  });
  return (
    <Dialog open={open} onClose={onClose} title="New scenario"
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={() => create.mutate()} disabled={!modelId || !name}>Create</button></>}>
      <Field label="Model" htmlFor="ns-model">
        <select id="ns-model" value={modelId} onChange={(e) => setModelId(e.target.value)}>
          <option value="">Select…</option>{models.map((m) => <option key={m.id} value={m.id}>{m.name} ({DISCIPLINES[m.discipline] ?? m.discipline})</option>)}
        </select>
      </Field>
      <Field label="Name" htmlFor="ns-name"><input id="ns-name" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label="Engine" htmlFor="ns-engine">
        <select id="ns-engine" value={engine} onChange={(e) => setEngine(e.target.value)}>
          {options.map((e) => <option key={e.capability.engine_id} value={e.capability.engine_id}>{e.capability.name} ({e.availability[0]?.availability_label})</option>)}
        </select>
      </Field>
      <Field label="Description" htmlFor="ns-desc"><textarea id="ns-desc" value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      <p className="muted small">The scenario starts with default controls; edit it to describe the change.</p>
      <ErrorNote error={create.error} />
    </Dialog>
  );
}

function Optimisations() {
  const pid = useProjectId();
  const studies = useOptimisations(pid);
  const [open, setOpen] = useState(false);
  return (
    <Card title="Optimisation studies" intro="Deterministic search over real engine runs within a budget. Results name the best tested candidate; untested combinations are not claimed."
      actions={<button className="btn btn-small" onClick={() => setOpen(true)}>New study</button>}>
      {!studies.data?.length && <p className="muted">No studies yet.</p>}
      <div className="stack">{studies.data?.map((s) => <Study key={s.id} s={s} />)}</div>
      <NewStudy open={open} onClose={() => setOpen(false)} />
    </Card>
  );
}

function Study({ s }: { s: Optimisation }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const adopt = useMutation({
    mutationFn: (i: number) => api.post(`/optimisations/${s.id}/adopt`, { candidate_index: i, scenario_name: name || `${s.name} candidate ${i}` }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scenarios"] }),
  });
  const def = s.definition as { objective_metric: string; objective_entity?: string; direction: string; budget: number; method: string };
  return (
    <details className="card" style={{ background: "var(--surface-2)" }} open>
      <summary>
        <strong>{s.name}</strong> <StatusBadge status={s.status} /> <span className="muted small">{def.direction} {def.objective_metric}{def.objective_entity ? `[${def.objective_entity}]` : ""} · {def.method} · budget {def.budget}</span>
      </summary>
      {s.stop_reason && <p className="small" style={{ marginTop: 6 }}><strong>Stopped:</strong> {s.stop_reason}</p>}
      <div className="table-wrap" style={{ marginTop: 8 }}>
        <table className="data">
          <thead><tr><th>#</th><th>Values</th><th className="num">Objective</th><th>Constraints</th><th>Run</th><th /></tr></thead>
          <tbody>
            {s.candidates.map((c) => (
              <tr key={c.index}>
                <td>{c.index}{s.best_candidate_index === c.index && <div><StatusBadge status="ok" label="Best tested" /></div>}</td>
                <td className="small mono">{Object.entries(c.values).map(([k, v]) => `${k.split(".").slice(-1)[0]}=${JSON.stringify(v)}`).join(", ")}</td>
                <td className="num">{c.objective !== null ? fmtNum(c.objective) : c.objective_status}</td>
                <td className="small">{c.constraints.length ? c.constraints.map((k, i) => <div key={i}><StatusBadge status={k.status} /> {k.metric_id} {k.op} {k.value}</div>) : "—"}</td>
                <td className="small">{c.run_id ? <Link to={`/p/${s.project_id}/runs/${c.run_id}`}>{c.run_state}</Link> : c.run_state}</td>
                <td>{c.run_state === "completed" && <button className="btn btn-small" onClick={() => adopt.mutate(c.index)}>Adopt</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <label className="small" htmlFor={`ad-${s.id}`}>Name for an adopted candidate</label>
        <input id={`ad-${s.id}`} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <ErrorNote error={adopt.error} />
      <p className="muted small" style={{ marginTop: 6 }}>{s.presentation_note}</p>
    </details>
  );
}

function NewStudy({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pid = useProjectId();
  const qc = useQueryClient();
  const scenarios = useScenarios(pid);
  const defs = useMetricDefs();
  const models = useModels(pid);
  const [f, setF] = useState({ name: "", base: "", metric: "", entity: "", direction: "minimise", path: "", values: "", budget: "6", method: "grid" });
  const base = scenarios.data?.find((s) => s.current_revision_id === f.base);
  const disc = models.data?.find((m) => m.id === base?.domain_model_id)?.discipline;
  const create = useMutation({
    mutationFn: () => api.post(`/projects/${pid}/optimisations`, {
      name: f.name, base_scenario_revision_id: f.base, objective_metric: f.metric, objective_entity: f.entity || null, direction: f.direction,
      variables: [{ path: f.path, values: f.values.split(",").map((v) => { const n = Number(v.trim()); return Number.isNaN(n) ? v.trim() : n; }) }],
      constraints: [], budget: Number(f.budget), method: f.method,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["optimisations", pid] }); onClose(); },
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <Dialog open={open} onClose={onClose} title="New optimisation study"
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={() => create.mutate()} disabled={create.isPending}>{create.isPending ? "Running candidates…" : "Start study"}</button></>}>
      <Field label="Name" htmlFor="os-name"><input id="os-name" value={f.name} onChange={set("name")} /></Field>
      <Field label="Base scenario" htmlFor="os-base">
        <select id="os-base" value={f.base} onChange={set("base")}><option value="">Select…</option>{scenarios.data?.map((s) => <option key={s.id} value={s.current_revision_id ?? ""}>{s.name}</option>)}</select>
      </Field>
      <div className="grid grid-3">
        <Field label="Objective metric" htmlFor="os-metric">
          <select id="os-metric" value={f.metric} onChange={set("metric")}><option value="">Select…</option>{defs.data?.filter((d) => !disc || d.discipline === disc).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select>
        </Field>
        <Field label="Entity" htmlFor="os-entity" hint="For per-entity metrics"><input id="os-entity" value={f.entity} onChange={set("entity")} /></Field>
        <Field label="Direction" htmlFor="os-dir"><select id="os-dir" value={f.direction} onChange={set("direction")}><option value="minimise">Minimise</option><option value="maximise">Maximise</option></select></Field>
      </div>
      <div className="grid grid-2">
        <Field label="Variable (scenario control path)" htmlFor="os-path" hint="e.g. controls.capacity_changes.bay"><input id="os-path" value={f.path} onChange={set("path")} /></Field>
        <Field label="Values to test" htmlFor="os-values" hint="Comma separated"><input id="os-values" value={f.values} onChange={set("values")} /></Field>
      </div>
      <div className="grid grid-2">
        <Field label="Evaluation budget" htmlFor="os-budget"><input id="os-budget" type="number" min={1} max={60} value={f.budget} onChange={set("budget")} /></Field>
        <Field label="Method" htmlFor="os-method"><select id="os-method" value={f.method} onChange={set("method")}><option value="grid">Grid (exhaustive, fixed order)</option><option value="coordinate">Coordinate search</option></select></Field>
      </div>
      <ErrorNote error={create.error} />
      {create.data != null && <JsonView value={create.data} />}
    </Dialog>
  );
}
