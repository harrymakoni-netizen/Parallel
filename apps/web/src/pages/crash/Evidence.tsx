import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, sameOrigin } from "../../api/client";
import { useEvidence, useObservations, useVehicles } from "../../api/hooks";
import type { Case, Evidence, Observation } from "../../api/types";
import { Card, Dialog, ErrorNote, Field, PageHead, StatusBadge, fmtDate, fmtNum, humanise } from "../../components/ui";
import { CaseBar, NoCase, useSelectedCase } from "./CaseSelect";

/** Shorten at a word boundary with an ellipsis, never mid-word. */
function clip(text: string, n: number): string {
  if (text.length <= n) return text;
  const cut = text.slice(0, n);
  return `${cut.slice(0, Math.max(cut.lastIndexOf(" "), n * 0.6)).trimEnd()}…`;
}

export function EvidencePage() {
  const { current } = useSelectedCase();
  return (
    <div className="page">
      <PageHead title="Evidence" intro="Originals are preserved and hashed at receipt. Redactions, enhancements and annotations are separate derivatives. Observations record what evidence shows; interpretations record reasoning and cite the observations they use." />
      <CaseBar />
      {current ? <CaseEvidence c={current} /> : <NoCase />}
    </div>
  );
}

function CaseEvidence({ c }: { c: Case }) {
  const ev = useEvidence(c.id);
  const obs = useObservations(c.id);
  const veh = useVehicles(c.id);
  const canEdit = c.my_role === "investigator";
  return (
    <div className="stack">
      <Card title="Evidence items" actions={canEdit && <AddEvidence caseId={c.id} />}>
        <div className="stack">{ev.data?.map((e) => <EvidenceRow key={e.id} e={e} canEdit={canEdit} />)}</div>
        {!ev.data?.length && <p className="muted">No evidence recorded.</p>}
      </Card>
      <div className="grid grid-2">
        <Card title="Observations" intro="What the evidence shows, with source, location, measurement and uncertainty." actions={canEdit && <AddObservation c={c} type="observation" evidence={ev.data ?? []} observations={obs.data ?? []} />}>
          <ObservationList items={(obs.data ?? []).filter((o) => o.statement_type === "observation")} evidence={ev.data ?? []} />
        </Card>
        <Card title="Interpretations" intro="Analyst reasoning. Each cites the observations it reasons from." actions={canEdit && <><SkidSpeed c={c} observations={obs.data ?? []} /><AddObservation c={c} type="interpretation" evidence={ev.data ?? []} observations={obs.data ?? []} /></>}>
          <ObservationList items={(obs.data ?? []).filter((o) => o.statement_type === "interpretation")} evidence={ev.data ?? []} all={obs.data ?? []} />
        </Card>
      </div>
      <Card title="Vehicles">
        <table className="data">
          <thead><tr><th>Vehicle</th><th className="num">Mass (kg)</th><th className="num">Length (m)</th><th className="num">Width (m)</th><th>Data source</th></tr></thead>
          <tbody>{veh.data?.map((v) => <tr key={v.id}><td>{v.label}</td><td className="num">{fmtNum(v.mass_kg)}</td><td className="num">{fmtNum(v.length_m)}</td><td className="num">{fmtNum(v.width_m)}</td><td>{v.data_source}</td></tr>)}</tbody>
        </table>
      </Card>
    </div>
  );
}

function EvidenceRow({ e, canEdit }: { e: Evidence; canEdit: boolean }) {
  const qc = useQueryClient();
  const [log, setLog] = useState(false);
  const [deriv, setDeriv] = useState(false);
  const verify = useMutation({ mutationFn: () => api.post<{ matches: boolean; current_sha256: string; note: string }>(`/evidence/${e.id}/verify`) });
  const open = useMutation({ mutationFn: async () => { const u = await api.get<{ url: string }>(`/evidence/${e.id}/original-url`); window.open(sameOrigin(u.url), "_blank", "noopener"); qc.invalidateQueries({ queryKey: ["handling", e.id] }); } });
  return (
    <div className="card" style={{ background: "var(--surface-2)" }}>
      <div className="row">
        <strong>{e.title}</strong><span className="badge">{e.kind}</span>
        <div className="spacer" />
        {e.source_asset_id && <button className="btn btn-small" onClick={() => open.mutate()}>Open original</button>}
        {e.source_asset_id && <button className="btn btn-small" onClick={() => verify.mutate()}>Verify hash</button>}
        <button className="btn btn-small btn-ghost" onClick={() => setLog(true)}>Handling log</button>
        {canEdit && e.source_asset_id && <button className="btn btn-small btn-ghost" onClick={() => setDeriv(true)}>Derivatives</button>}
      </div>
      <div className="small muted">Captured by {e.captured_by ?? "not stated"} · {e.claimed_captured_at ? `capture time stated as ${fmtDate(e.claimed_captured_at)} (not verified)` : "capture time not stated"} · received {fmtDate(e.created_at)}</div>
      {e.original_sha256 && <div className="mono small" style={{ marginTop: 4 }}>SHA-256 {e.original_sha256}</div>}
      {verify.data && (
        <div className={`note ${verify.data.matches ? "" : "error"}`} style={{ marginTop: 6 }}>
          <StatusBadge status={verify.data.matches ? "ok" : "failed"} label={verify.data.matches ? "Unchanged since receipt" : "Hash differs"} /> {verify.data.note}
        </div>
      )}
      <ErrorNote error={verify.error ?? open.error} />
      <HandlingLog open={log} onClose={() => setLog(false)} id={e.id} />
      <Derivatives open={deriv} onClose={() => setDeriv(false)} e={e} />
    </div>
  );
}

function HandlingLog({ open, onClose, id }: { open: boolean; onClose: () => void; id: string }) {
  const q = useQuery({ queryKey: ["handling", id], enabled: open, queryFn: () => api.get<{ id: string; action: string; details: Record<string, unknown>; created_at: string }[]>(`/evidence/${id}/handling`) });
  return (
    <Dialog open={open} onClose={onClose} title="Handling log" footer={<button className="btn" onClick={onClose}>Close</button>}>
      <table className="data"><thead><tr><th>When</th><th>Action</th><th>Details</th></tr></thead>
        <tbody>{q.data?.map((h) => <tr key={h.id}><td className="small">{fmtDate(h.created_at)}</td><td>{humanise(h.action)}</td><td className="small mono">{Object.entries(h.details).filter(([, v]) => v != null).map(([k, v]) => `${k}: ${String(v)}`).join(" · ")}</td></tr>)}</tbody>
      </table>
    </Dialog>
  );
}

function Derivatives({ open, onClose, e }: { open: boolean; onClose: () => void; e: Evidence }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["derivs", e.id], enabled: open, queryFn: () => api.get<{ id: string; kind: string; method: string; source_sha256: string; created_at: string }[]>(`/evidence/${e.id}/derivatives`) });
  const [kind, setKind] = useState("redaction");
  const [method, setMethod] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const add = useMutation({
    mutationFn: () => { const f = new FormData(); f.append("kind", kind); f.append("method", method); f.append("file", file!); return api.upload(`/evidence/${e.id}/derivatives`, f); },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["derivs", e.id] }),
  });
  return (
    <Dialog open={open} onClose={onClose} title="Derivatives" footer={<button className="btn" onClick={onClose}>Close</button>}>
      <p className="muted small">A derivative is a new file linked to the original's hash. The original is never modified.</p>
      <ul>{list.data?.map((d) => <li key={d.id}>{humanise(d.kind)} — {d.method} <span className="muted small">({fmtDate(d.created_at)})</span></li>)}</ul>
      <div className="grid grid-2">
        <Field label="Kind" htmlFor="dv-kind"><select id="dv-kind" value={kind} onChange={(x) => setKind(x.target.value)}>{["redaction", "enhancement", "annotation_overlay", "crop", "frame_extract"].map((k) => <option key={k} value={k}>{humanise(k)}</option>)}</select></Field>
        <Field label="File" htmlFor="dv-file"><input id="dv-file" type="file" onChange={(x) => setFile(x.target.files?.[0] ?? null)} /></Field>
      </div>
      <Field label="Method" htmlFor="dv-method" hint="What was done, with which tool and settings"><input id="dv-method" value={method} onChange={(x) => setMethod(x.target.value)} /></Field>
      <button className="btn btn-primary" onClick={() => add.mutate()} disabled={!file || method.length < 3}>Add derivative</button>
      <ErrorNote error={add.error} />
    </Dialog>
  );
}

function AddEvidence({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ kind: "image", title: "", description: "", captured_by: "", claimed_captured_at: "" });
  const [file, setFile] = useState<File | null>(null);
  const add = useMutation({
    mutationFn: () => {
      const d = new FormData();
      Object.entries(f).forEach(([k, v]) => v && d.append(k, v));
      if (file) d.append("file", file);
      return api.upload(`/cases/${caseId}/evidence`, d);
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["evidence", caseId] }); setOpen(false); },
  });
  return (
    <>
      <button className="btn btn-small btn-primary" onClick={() => setOpen(true)}>Add evidence</button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Add evidence"
        footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button><button className="btn btn-primary" onClick={() => add.mutate()} disabled={add.isPending}>Record evidence</button></>}>
        <div className="grid grid-2">
          <Field label="Kind" htmlFor="ae-kind"><select id="ae-kind" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{["image", "video", "document", "vehicle_data", "measurement", "other"].map((k) => <option key={k} value={k}>{humanise(k)}</option>)}</select></Field>
          <Field label="Original file" htmlFor="ae-file"><input id="ae-file" type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>
        </div>
        <Field label="Title" htmlFor="ae-title"><input id="ae-title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="Description" htmlFor="ae-desc"><textarea id="ae-desc" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <div className="grid grid-2">
          <Field label="Captured by" htmlFor="ae-by"><input id="ae-by" value={f.captured_by} onChange={(e) => setF({ ...f, captured_by: e.target.value })} /></Field>
          <Field label="Claimed capture time" htmlFor="ae-at" hint="As stated by the source; not verified"><input id="ae-at" type="datetime-local" value={f.claimed_captured_at} onChange={(e) => setF({ ...f, claimed_captured_at: e.target.value })} /></Field>
        </div>
        <ErrorNote error={add.error} />
      </Dialog>
    </>
  );
}

function ObservationList({ items, evidence, all }: { items: Observation[]; evidence: Evidence[]; all?: Observation[] }) {
  const ev = new Map(evidence.map((e) => [e.id, e.title]));
  const text = new Map((all ?? []).map((o) => [o.id, o.text]));
  if (!items.length) return <p className="muted">None recorded.</p>;
  return (
    <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {items.map((o) => {
        const u = o.uncertainty as { kind?: string; value?: number; lower?: number; upper?: number; unit?: string } | null;
        return (
          <li key={o.id} className="card" style={{ background: "var(--surface-2)", padding: 10 }}>
            <div className="row"><span className="badge">{humanise(o.kind)}</span><StatusBadge status={o.review_status === "unreviewed" ? "info" : o.review_status} label={o.review_status} /></div>
            <p style={{ marginTop: 6 }}>{o.text}</p>
            <div className="small muted">
              {o.measurement_value != null && <>Measured {fmtNum(o.measurement_value)} {o.measurement_unit}{u?.kind === "absolute" ? ` ± ${fmtNum(u.value)} ${u.unit ?? ""}` : ""} · </>}
              {u?.kind === "range" && <>Range {fmtNum(u.lower)}–{fmtNum(u.upper)} {u.unit} · </>}
              {o.evidence_item_id && <>From “{ev.get(o.evidence_item_id)}” · </>}
              {o.position && <>At ({o.position.map((x) => fmtNum(x, 3)).join(", ")}) m · </>}
              {o.based_on.length > 0 && <>Based on: {o.based_on.map((b) => `“${clip(text.get(b) ?? b, 60)}”`).join(", ")}</>}
            </div>
            {o.limitation && <div className="small"><strong>Limitation:</strong> {o.limitation}</div>}
          </li>
        );
      })}
    </ul>
  );
}

function AddObservation({ c, type, evidence, observations }: { c: Case; type: "observation" | "interpretation"; evidence: Evidence[]; observations: Observation[] }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ kind: "skid_mark", text: "", evidence_item_id: "", x: "", y: "", value: "", unit: "m", unc: "", limitation: "" });
  const [basis, setBasis] = useState<string[]>([]);
  const add = useMutation({
    mutationFn: () => api.post(`/cases/${c.id}/observations`, {
      statement_type: type, kind: f.kind, text: f.text, evidence_item_id: f.evidence_item_id || null,
      position: f.x && f.y ? [Number(f.x), Number(f.y)] : null,
      measurement_value: f.value ? Number(f.value) : null, measurement_unit: f.value ? f.unit : null,
      uncertainty: f.unc ? { kind: "absolute", value: Number(f.unc), unit: "m" } : null,
      limitation: f.limitation, based_on: type === "interpretation" ? basis : [],
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["observations", c.id] }); setOpen(false); },
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <>
      <button className="btn btn-small" onClick={() => setOpen(true)}>Add {type}</button>
      <Dialog open={open} onClose={() => setOpen(false)} title={`Add ${type}`}
        footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button><button className="btn btn-primary" onClick={() => add.mutate()}>Save</button></>}>
        <Field label="Kind" htmlFor="ao-kind"><select id="ao-kind" value={f.kind} onChange={set("kind")}>{["skid_mark", "yaw_mark", "debris", "final_position", "road_condition", "visibility", "vehicle_damage", "scene_measurement", "other"].map((k) => <option key={k} value={k}>{humanise(k)}</option>)}</select></Field>
        <Field label={type === "observation" ? "What the evidence shows" : "Reasoning"} htmlFor="ao-text"><textarea id="ao-text" value={f.text} onChange={set("text")} /></Field>
        {type === "observation" ? (
          <>
            <Field label="Evidence source" htmlFor="ao-ev"><select id="ao-ev" value={f.evidence_item_id} onChange={set("evidence_item_id")}><option value="">Select…</option>{evidence.map((e) => <option key={e.id} value={e.id}>{e.title}</option>)}</select></Field>
            <div className="grid grid-2">
              <Field label="Position x (m)" htmlFor="ao-x"><input id="ao-x" inputMode="decimal" value={f.x} onChange={set("x")} /></Field>
              <Field label="Position y (m)" htmlFor="ao-y"><input id="ao-y" inputMode="decimal" value={f.y} onChange={set("y")} /></Field>
            </div>
            <div className="grid grid-3">
              <Field label="Measurement" htmlFor="ao-val"><input id="ao-val" inputMode="decimal" value={f.value} onChange={set("value")} /></Field>
              <Field label="Unit" htmlFor="ao-unit"><input id="ao-unit" value={f.unit} onChange={set("unit")} /></Field>
              <Field label="± uncertainty (m)" htmlFor="ao-unc"><input id="ao-unc" inputMode="decimal" value={f.unc} onChange={set("unc")} /></Field>
            </div>
          </>
        ) : (
          <fieldset style={{ border: 0, padding: 0 }}><legend><strong>Based on observations</strong></legend>
            {observations.filter((o) => o.statement_type === "observation").map((o) => (
              <label key={o.id} className="row small"><input type="checkbox" checked={basis.includes(o.id)} onChange={(e) => setBasis(e.target.checked ? [...basis, o.id] : basis.filter((x) => x !== o.id))} />{clip(o.text, 80)}</label>
            ))}
          </fieldset>
        )}
        <Field label="Uncertainty or limitation" htmlFor="ao-lim"><input id="ao-lim" value={f.limitation} onChange={set("limitation")} /></Field>
        <ErrorNote error={add.error} />
      </Dialog>
    </>
  );
}

function SkidSpeed({ c, observations }: { c: Case; observations: Observation[] }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const skids = observations.filter((o) => o.kind === "skid_mark" && o.statement_type === "observation" && o.measurement_unit === "m");
  const [f, setF] = useState({ observation_id: "", friction_low: "0.65", friction_high: "0.8", friction_basis: "" });
  const calc = useMutation({
    mutationFn: () => api.post(`/cases/${c.id}/calculations/skid-speed`, { ...f, friction_low: Number(f.friction_low), friction_high: Number(f.friction_high) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["observations", c.id] }); setOpen(false); },
  });
  return (
    <>
      <button className="btn btn-small" onClick={() => setOpen(true)} disabled={!skids.length}>Skid-to-stop speed</button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Skid-to-stop speed (v = √(2 μ g d))"
        footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button><button className="btn btn-primary" onClick={() => calc.mutate()}>Calculate</button></>}>
        <p className="muted small">Valid only for a full skid to rest on a level surface without impact during the skid. The result is recorded as an interpretation, with its limits. It is not a determination of fault.</p>
        <Field label="Skid-mark observation" htmlFor="sk-obs"><select id="sk-obs" value={f.observation_id} onChange={(e) => setF({ ...f, observation_id: e.target.value })}><option value="">Select…</option>{skids.map((o) => <option key={o.id} value={o.id}>{clip(o.text, 60)} ({o.measurement_value} m)</option>)}</select></Field>
        <div className="grid grid-2">
          <Field label="Friction low" htmlFor="sk-lo"><input id="sk-lo" inputMode="decimal" value={f.friction_low} onChange={(e) => setF({ ...f, friction_low: e.target.value })} /></Field>
          <Field label="Friction high" htmlFor="sk-hi"><input id="sk-hi" inputMode="decimal" value={f.friction_high} onChange={(e) => setF({ ...f, friction_high: e.target.value })} /></Field>
        </div>
        <Field label="Source of the friction range" htmlFor="sk-basis"><input id="sk-basis" value={f.friction_basis} onChange={(e) => setF({ ...f, friction_basis: e.target.value })} /></Field>
        <ErrorNote error={calc.error} />
      </Dialog>
    </>
  );
}
