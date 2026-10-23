import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import { useHypotheses, useObservations, useVehicles } from "../../api/hooks";
import type { Case, Hypothesis, Observation } from "../../api/types";
import { Card, Dialog, ErrorNote, Field, PageHead, StatusBadge, fmtNum } from "../../components/ui";
import { CaseBar, NoCase, useSelectedCase } from "./CaseSelect";

export function Hypotheses() {
  const { current } = useSelectedCase();
  return (
    <div className="page">
      <PageHead title="Hypotheses" intro="Proposed event sequences, tested against evidence. Hypotheses are compared by their evidence links and by defined residuals from engine analyses. Parallel never computes a probability that a hypothesis is true, and never determines fault." />
      <CaseBar />
      {current ? <CaseHypotheses c={current} /> : <NoCase />}
    </div>
  );
}

interface CompareRow { hypothesis_id: string; label: string; status: string; supporting: string[]; contradicting: string[]; unresolved_questions: string[]; rms_residual_m: number | null; targets_within_uncertainty: number | null; targets_total: number | null }

function CaseHypotheses({ c }: { c: Case }) {
  const hyps = useHypotheses(c.id);
  const obs = useObservations(c.id);
  const cmp = useQuery({ queryKey: ["hyp-cmp", c.id], queryFn: () => api.get<{ hypotheses: CompareRow[]; basis: string }>(`/cases/${c.id}/hypotheses-comparison`) });
  const [adding, setAdding] = useState(false);
  const canEdit = c.my_role === "investigator";
  return (
    <div className="stack">
      <Card title="Side by side" intro={cmp.data?.basis} actions={canEdit && <button className="btn btn-small btn-primary" onClick={() => setAdding(true)}>New hypothesis</button>}>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Hypothesis</th><th className="num">Supporting</th><th className="num">Contradicting</th><th className="num">Unresolved</th><th className="num">RMS residual (m)</th><th>Targets within uncertainty</th></tr></thead>
            <tbody>
              {cmp.data?.hypotheses.map((h) => (
                <tr key={h.hypothesis_id}>
                  <td>{h.label}</td><td className="num">{h.supporting.length}</td><td className="num">{h.contradicting.length}</td><td className="num">{h.unresolved_questions.length}</td>
                  <td className="num">{h.rms_residual_m != null ? fmtNum(h.rms_residual_m) : <span className="muted">not evaluated</span>}</td>
                  <td>{h.targets_total != null ? `${h.targets_within_uncertainty} of ${h.targets_total}` : <span className="muted">needs a collision-engine analysis</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      {hyps.data?.map((h) => <HypothesisCard key={h.id} h={h} c={c} observations={obs.data ?? []} />)}
      <NewHypothesis open={adding} onClose={() => setAdding(false)} c={c} />
    </div>
  );
}

function HypothesisCard({ h, c, observations }: { h: Hypothesis; c: Case; observations: Observation[] }) {
  const qc = useQueryClient();
  const text = new Map(observations.map((o) => [o.id, o]));
  const [link, setLink] = useState({ observation_id: "", relation: "supports", rationale: "" });
  const pb = useQuery({ queryKey: ["playback-src", h.id], queryFn: () => api.get<{ sources: { source: string; label: string; run_id?: string }[] }>(`/hypotheses/${h.id}/playback`) });
  const add = useMutation({ mutationFn: () => api.post(`/hypotheses/${h.id}/links`, link), onSuccess: () => { qc.invalidateQueries({ queryKey: ["hypotheses", c.id] }); qc.invalidateQueries({ queryKey: ["hyp-cmp", c.id] }); } });
  const fit = h.content.evidence_fit as { summary?: string } | undefined;
  return (
    <Card title={h.label} intro={h.content.description} actions={<StatusBadge status="info" label={h.status} />}>
      {h.content.sequence && h.content.sequence.length > 0 && (
        <><h3>Proposed sequence</h3><ol>{h.content.sequence.map((s) => <li key={s.order}>{s.description}</li>)}</ol></>
      )}
      <div className="grid grid-2">
        <div>
          <h3>Evidence links</h3>
          <ul>
            {h.links.map((l) => (
              <li key={l.id}><StatusBadge status={l.relation === "supports" ? "ok" : l.relation === "contradicts" ? "warning" : "info"} label={l.relation} /> {text.get(l.observation_id)?.text ?? l.observation_id}
                <div className="muted small">{l.rationale}</div></li>
            ))}
            {!h.links.length && <li className="muted">No links yet.</li>}
          </ul>
        </div>
        <div>
          <h3>Unresolved questions</h3>
          <ul>{(h.content.unresolved_questions ?? []).map((q) => <li key={q}>{q}</li>)}</ul>
          <h3>Engine analysis</h3>
          <p className="small">{fit?.summary ?? "No collision-engine analysis attached. A licensed Virtual CRASH or PC-Crash installation is needed to test this hypothesis against the measured targets."}</p>
          <h3>Playback sources</h3>
          <ul className="small">{pb.data?.sources.map((s, i) => <li key={i}>{s.label}{s.run_id ? ` (run ${s.run_id.slice(0, 8)})` : ""}</li>)}{!pb.data?.sources.length && <li className="muted">None.</li>}</ul>
        </div>
      </div>
      {h.content.analyst_interpretation && <p className="small"><strong>Analyst interpretation:</strong> {h.content.analyst_interpretation}</p>}
      {c.my_role === "investigator" && (
        <form className="grid grid-4" style={{ alignItems: "end", marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <Field label="Observation" htmlFor={`ln-o-${h.id}`}><select id={`ln-o-${h.id}`} value={link.observation_id} onChange={(e) => setLink({ ...link, observation_id: e.target.value })}><option value="">Select…</option>{observations.map((o) => <option key={o.id} value={o.id}>{o.text.slice(0, 60)}</option>)}</select></Field>
          <Field label="Relation" htmlFor={`ln-r-${h.id}`}><select id={`ln-r-${h.id}`} value={link.relation} onChange={(e) => setLink({ ...link, relation: e.target.value })}><option value="supports">Supports</option><option value="contradicts">Contradicts</option><option value="context">Context</option></select></Field>
          <Field label="Rationale" htmlFor={`ln-w-${h.id}`}><input id={`ln-w-${h.id}`} value={link.rationale} onChange={(e) => setLink({ ...link, rationale: e.target.value })} /></Field>
          <button className="btn">Link evidence</button>
        </form>
      )}
      <ErrorNote error={add.error} />
    </Card>
  );
}

function NewHypothesis({ open, onClose, c }: { open: boolean; onClose: () => void; c: Case }) {
  const qc = useQueryClient();
  const veh = useVehicles(c.id);
  const [label, setLabel] = useState("");
  const [description, setDescription] = useState("");
  const [questions, setQuestions] = useState("");
  const create = useMutation({
    mutationFn: () => api.post(`/cases/${c.id}/hypotheses`, { label, content: { description, participant_ids: (veh.data ?? []).map((v) => v.id), unresolved_questions: questions.split("\n").filter(Boolean) } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["hypotheses", c.id] }); qc.invalidateQueries({ queryKey: ["hyp-cmp", c.id] }); onClose(); },
  });
  return (
    <Dialog open={open} onClose={onClose} title="New hypothesis"
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={() => create.mutate()}>Create</button></>}>
      <Field label="Label" htmlFor="nh-l"><input id="nh-l" value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
      <Field label="Description" htmlFor="nh-d"><textarea id="nh-d" value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      <Field label="Unresolved questions" htmlFor="nh-q" hint="One per line"><textarea id="nh-q" value={questions} onChange={(e) => setQuestions(e.target.value)} /></Field>
      <ErrorNote error={create.error} />
    </Dialog>
  );
}
