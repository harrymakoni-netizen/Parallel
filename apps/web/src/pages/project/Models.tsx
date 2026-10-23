import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import { useModelRevision, useModels, useScenes } from "../../api/hooks";
import type { DomainModel, ModelRevision } from "../../api/types";
import { Card, Dialog, Empty, ErrorNote, Field, JsonView, KV, PageHead, ProvenanceBadge, StatusBadge, fmtDate } from "../../components/ui";
import { DISCIPLINES } from "../common";
import { useProjectId } from "./ProjectLayout";

export function Models() {
  const pid = useProjectId();
  const models = useModels(pid);
  const [sel, setSel] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const current = models.data?.find((m) => m.id === sel) ?? models.data?.[0];
  return (
    <div className="page">
      <PageHead title="Models" intro="Prepared domain models: simplified, editable analytical geometry and parameters. Every input records its provenance; published revisions are immutable and edits create new revisions.">
        <button className="btn" onClick={() => setImporting(true)}>Import model file</button>
      </PageHead>
      <DeriveFromScene />
      {!models.data?.length && <Empty>No models yet. Derive one from a prepared scene, start from a template, or import a model file.</Empty>}
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 300px) minmax(0, 1fr)", alignItems: "start" }}>
        <div className="stack">
          {models.data?.map((m) => (
            <button key={m.id} className="list-item" style={{ font: "inherit", textAlign: "left" }} aria-pressed={current?.id === m.id} onClick={() => setSel(m.id)}>
              <div style={{ flex: 1 }}>
                <strong>{m.name}</strong>
                <div className="muted small">{DISCIPLINES[m.discipline] ?? m.discipline}{m.case_id ? " · case-scoped" : ""}</div>
              </div>
            </button>
          ))}
        </div>
        {current && <ModelDetail model={current} />}
      </div>
      <ImportModel open={importing} onClose={() => setImporting(false)} />
    </div>
  );
}

function summarise(content: Record<string, unknown>) {
  return Object.entries(content).filter(([, v]) => Array.isArray(v)).map(([k, v]) => [k.replace(/_/g, " "), (v as unknown[]).length] as [string, number]);
}

function provenanceCounts(v: unknown, out: Record<string, number> = {}): Record<string, number> {
  if (Array.isArray(v)) v.forEach((x) => provenanceCounts(x, out));
  else if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (typeof o.kind === "string" && ["measured", "imported", "assumption", "algorithm_estimate", "ai_suggested", "derived", "synthetic"].includes(o.kind)
      && Object.keys(o).every((k) => ["kind", "source", "source_ref", "derived_from", "recorded_by", "recorded_at", "uncertainty", "note", "adopted_by", "adopted_at"].includes(k))) {
      out[o.kind] = (out[o.kind] ?? 0) + 1;
    } else Object.values(o).forEach((x) => provenanceCounts(x, out));
  }
  return out;
}

function ModelDetail({ model }: { model: DomainModel }) {
  const qc = useQueryClient();
  const rev = useModelRevision(model.current_revision_id);
  const revs = useQuery({ queryKey: ["model-revs", model.id], queryFn: () => api.get<ModelRevision[]>(`/models/${model.id}/revisions`) });
  const [editing, setEditing] = useState(false);
  const publish = useMutation({
    mutationFn: () => api.post(`/model-revisions/${rev.data!.id}/publish`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["model-rev"] }); qc.invalidateQueries({ queryKey: ["model-revs", model.id] }); },
  });
  const r = rev.data;
  if (!r) return null;
  const env = r.content as { name: string; content: Record<string, unknown>; scene_revision_id?: string; provenance?: { kind: string; note?: string } };
  const issues = r.validation.issues ?? [];
  const prov = provenanceCounts(env.content);
  return (
    <div className="stack">
      <Card title={model.name} intro={`${DISCIPLINES[model.discipline] ?? model.discipline} · revision ${r.number} (${r.status})`}
        actions={<>
          {r.status === "draft" && <button className="btn btn-small" onClick={() => publish.mutate()}>Publish revision</button>}
          <button className="btn btn-small" onClick={() => setEditing(true)}>Edit as JSON</button>
        </>}>
        <ErrorNote error={publish.error} />
        <div className="grid grid-2">
          <KV items={[
            ["Validation", <StatusBadge key="v" status={r.validation.ok === false ? "failed" : "ok"} label={r.validation.ok === false ? "Errors" : "Valid"} />],
            ["Scene revision", env.scene_revision_id ? <span className="mono">{env.scene_revision_id.slice(0, 12)}</span> : "Not linked"],
            ["Model provenance", env.provenance ? <ProvenanceBadge kind={env.provenance.kind} /> : "—"],
            ["Content hash", <span key="h" className="mono">{r.content_sha256.slice(0, 16)}…</span>],
          ]} />
          <div>
            <h3>Objects</h3>
            <KV items={summarise(env.content).map(([k, n]) => [k, n])} />
          </div>
        </div>
        <h3 style={{ marginTop: 12 }}>Input provenance</h3>
        <div className="row">{Object.entries(prov).map(([k, n]) => <span key={k} className="row" style={{ gap: 4 }}><ProvenanceBadge kind={k} /> {n}</span>)}</div>
        {issues.length > 0 && (
          <>
            <h3 style={{ marginTop: 12 }}>Validation issues</h3>
            <ul>{issues.map((i, k) => <li key={k}><StatusBadge status={i.severity} /> {i.message} {i.path && <span className="mono muted">{i.path}</span>}</li>)}</ul>
          </>
        )}
      </Card>
      <Card title="Revisions">
        <table className="data">
          <thead><tr><th>#</th><th>Status</th><th>Message</th><th>Created</th></tr></thead>
          <tbody>{revs.data?.map((x) => <tr key={x.id}><td>{x.number}</td><td><StatusBadge status={x.status === "published" ? "ok" : "queued"} label={x.status} /></td><td className="small">{x.message}</td><td className="small">{fmtDate(x.created_at)}</td></tr>)}</tbody>
        </table>
      </Card>
      <EditModel open={editing} onClose={() => setEditing(false)} model={model} revision={r} />
    </div>
  );
}

function EditModel({ open, onClose, model, revision }: { open: boolean; onClose: () => void; model: DomainModel; revision: ModelRevision }) {
  const qc = useQueryClient();
  const [text, setText] = useState(() => JSON.stringify(revision.content, null, 2));
  const [message, setMessage] = useState("");
  const save = useMutation({
    mutationFn: () => api.post(`/models/${model.id}/revisions`, { content: JSON.parse(text), message, base_revision_id: revision.id }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["models"] }); qc.invalidateQueries({ queryKey: ["model-rev"] }); qc.invalidateQueries({ queryKey: ["model-revs", model.id] }); onClose(); },
  });
  return (
    <Dialog open={open} onClose={onClose} title={`Edit ${model.name}`}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={() => save.mutate()}>Save revision</button></>}>
      <p className="muted small">A published revision is never changed: saving creates a new revision. Runs keep referring to the revisions they used.</p>
      <Field label="Model envelope (JSON)" htmlFor="em-json"><textarea id="em-json" className="mono" style={{ minHeight: 320 }} value={text} onChange={(e) => setText(e.target.value)} /></Field>
      <Field label="Change note" htmlFor="em-msg"><input id="em-msg" value={message} onChange={(e) => setMessage(e.target.value)} /></Field>
      <ErrorNote error={save.error} />
    </Dialog>
  );
}

function ImportModel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pid = useProjectId();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const create = useMutation({
    mutationFn: () => api.post(`/projects/${pid}/models`, { name, content: JSON.parse(text) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["models", pid] }); onClose(); },
  });
  const schemas = useQuery({ queryKey: ["model-schemas"], enabled: open, queryFn: () => api.get<Record<string, unknown>>("/models/schemas") });
  return (
    <Dialog open={open} onClose={onClose} title="Import a domain model"
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={() => create.mutate()} disabled={!name || !text}>Import</button></>}>
      <Field label="Name" htmlFor="im-name"><input id="im-name" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label="Model envelope JSON" htmlFor="im-file" hint="A DomainModelEnvelope: discipline, name, content and provenance for every important input.">
        <input id="im-file" type="file" accept=".json" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setText(await f.text()); }} />
      </Field>
      <textarea aria-label="Model JSON" className="mono" style={{ minHeight: 160 }} value={text} onChange={(e) => setText(e.target.value)} />
      {schemas.data && <details><summary>Schema reference</summary><JsonView value={schemas.data.model_kinds} /></details>}
      <ErrorNote error={create.error} />
    </Dialog>
  );
}

function DeriveFromScene() {
  const pid = useProjectId();
  const qc = useQueryClient();
  const scenes = useScenes(pid);
  const [rev, setRev] = useState("");
  const [name, setName] = useState("Movement model");
  const derive = useMutation({
    mutationFn: () => api.post(`/scene-revisions/${rev}/derive-model`, { discipline: "pedestrian", name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["models", pid] }),
  });
  if (!scenes.data?.length) return null;
  return (
    <details className="card" style={{ marginBottom: 14 }}>
      <summary><strong>Derive a pedestrian model from a scene</strong> <span className="muted small">uses prepared walkable areas, obstacles, exits, start zones and route segments; captured appearance is never treated as walkable geometry</span></summary>
      <form className="row" style={{ marginTop: 10, alignItems: "end" }} onSubmit={(e) => { e.preventDefault(); derive.mutate(); }}>
        <Field label="Scene" htmlFor="dv-scene">
          <select id="dv-scene" value={rev} onChange={(e) => setRev(e.target.value)} required>
            <option value="">Select…</option>
            {scenes.data.map((s) => s.current_revision_id && <option key={s.id} value={s.current_revision_id}>{s.name}</option>)}
          </select>
        </Field>
        <Field label="Model name" htmlFor="dv-name"><input id="dv-name" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <button className="btn btn-primary">Derive model</button>
      </form>
      <ErrorNote error={derive.error} />
    </details>
  );
}
