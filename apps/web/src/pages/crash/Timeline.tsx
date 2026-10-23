import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import { useHypotheses, useTimeline } from "../../api/hooks";
import type { Case } from "../../api/types";
import { Card, ErrorNote, Field, PageHead, fmtDate, fmtNum } from "../../components/ui";
import { CaseBar, NoCase, useSelectedCase } from "./CaseSelect";

export function Timeline() {
  const { current } = useSelectedCase();
  return (
    <div className="page">
      <PageHead title="Timeline" intro="Recorded events come from evidence, witness references and records. Events proposed by a hypothesis are shown only with that hypothesis, and are labelled as proposed." />
      <CaseBar />
      {current ? <CaseTimeline c={current} /> : <NoCase />}
    </div>
  );
}

function CaseTimeline({ c }: { c: Case }) {
  const [hyp, setHyp] = useState("");
  const hyps = useHypotheses(c.id);
  const events = useTimeline(c.id);
  const shown = (events.data ?? []).filter((e) => !e.hypothesis_id || e.hypothesis_id === hyp);
  const hname = new Map((hyps.data ?? []).map((h) => [h.id, h.label]));
  return (
    <div className="stack">
      <div className="row">
        <label htmlFor="tl-h" className="small">Show proposed events of</label>
        <select id="tl-h" value={hyp} onChange={(e) => setHyp(e.target.value)}>
          <option value="">No hypothesis (recorded events only)</option>
          {hyps.data?.map((h) => <option key={h.id} value={h.id}>{h.label}</option>)}
        </select>
      </div>
      <Card>
        <ol className="timeline">
          {shown.sort((a, b) => (a.t_offset_s ?? 0) - (b.t_offset_s ?? 0)).map((e) => (
            <li key={e.id} className={e.hypothesis_id ? "proposed" : ""}>
              <div className="mono small">{e.t_offset_s != null ? `t = ${fmtNum(e.t_offset_s)} s` : fmtDate(e.at)}{e.t_uncertainty_s ? ` ± ${fmtNum(e.t_uncertainty_s)} s` : ""}</div>
              <div>{e.description}</div>
              <div className="muted small">{e.hypothesis_id ? `Proposed by ${hname.get(e.hypothesis_id)}` : `Recorded · source: ${e.source_type}`}</div>
            </li>
          ))}
        </ol>
        {!shown.length && <p className="muted">No events.</p>}
      </Card>
      {c.my_role === "investigator" && <AddEvent c={c} hypotheses={hyps.data ?? []} />}
    </div>
  );
}

function AddEvent({ c, hypotheses }: { c: Case; hypotheses: { id: string; label: string }[] }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ t: "", u: "", description: "", source_type: "record", source_id: "", hypothesis_id: "" });
  const add = useMutation({
    mutationFn: () => api.post(`/cases/${c.id}/timeline`, {
      t_offset_s: f.t ? Number(f.t) : null, t_uncertainty_s: f.u ? Number(f.u) : null, description: f.description, source_type: f.source_type,
      source_id: f.source_type === "hypothesis" ? f.hypothesis_id : f.source_id || null, hypothesis_id: f.hypothesis_id || null,
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["timeline", c.id] }),
  });
  return (
    <Card title="Add event">
      <form className="grid grid-3" style={{ alignItems: "end" }} onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <Field label="Time offset (s)" htmlFor="ev-t"><input id="ev-t" inputMode="decimal" value={f.t} onChange={(e) => setF({ ...f, t: e.target.value })} /></Field>
        <Field label="± (s)" htmlFor="ev-u"><input id="ev-u" inputMode="decimal" value={f.u} onChange={(e) => setF({ ...f, u: e.target.value })} /></Field>
        <Field label="Source" htmlFor="ev-src"><select id="ev-src" value={f.source_type} onChange={(e) => setF({ ...f, source_type: e.target.value })}><option value="record">Record</option><option value="hypothesis">Hypothesis (proposed)</option></select></Field>
        <Field label="Description" htmlFor="ev-d"><input id="ev-d" required value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="Hypothesis" htmlFor="ev-h"><select id="ev-h" value={f.hypothesis_id} onChange={(e) => setF({ ...f, hypothesis_id: e.target.value })}><option value="">None (recorded)</option>{hypotheses.map((h) => <option key={h.id} value={h.id}>{h.label}</option>)}</select></Field>
        <button className="btn btn-primary">Add event</button>
      </form>
      <ErrorNote error={add.error} />
    </Card>
  );
}
