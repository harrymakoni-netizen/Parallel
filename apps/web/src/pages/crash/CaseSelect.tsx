import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import { useCases } from "../../api/hooks";
import type { Case } from "../../api/types";
import { Dialog, Empty, ErrorNote, Field } from "../../components/ui";
import { useProjectId } from "../project/ProjectLayout";

/** Cases visible to the user (explicit assignment only) and the selected one (?case=). */
export function useSelectedCase(): { cases: Case[] | undefined; current: Case | undefined; select: (id: string) => void } {
  const pid = useProjectId();
  const cases = useCases(pid);
  const [params, setParams] = useSearchParams();
  const current = cases.data?.find((c) => c.id === params.get("case")) ?? cases.data?.[0];
  return { cases: cases.data, current, select: (id) => setParams((p) => { p.set("case", id); return p; }) };
}

export function CaseBar() {
  const { cases, current, select } = useSelectedCase();
  const [open, setOpen] = useState(false);
  return (
    <div className="row" style={{ marginBottom: 14 }}>
      {cases && cases.length > 0 ? (
        <>
          <label htmlFor="case-sel" className="small">Case</label>
          <select id="case-sel" value={current?.id} onChange={(e) => select(e.target.value)}>
            {cases.map((c) => <option key={c.id} value={c.id}>{c.reference} · {c.title}</option>)}
          </select>
          {current?.training && <span className="badge badge-synthetic">Training case</span>}
          {current && <span className="badge">Your role: {current.my_role}</span>}
        </>
      ) : (
        <span className="muted">No cases are assigned to you in this project.</span>
      )}
      <div className="spacer" />
      <button className="btn btn-small" onClick={() => setOpen(true)}>New case</button>
      <NewCase open={open} onClose={() => setOpen(false)} onCreated={select} />
    </div>
  );
}

function NewCase({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const pid = useProjectId();
  const qc = useQueryClient();
  const [f, setF] = useState({ reference: "", title: "", location_note: "", training: false });
  const create = useMutation({
    mutationFn: () => api.post<Case>(`/projects/${pid}/cases`, f),
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ["cases", pid] }); onCreated(c.id); onClose(); },
  });
  return (
    <Dialog open={open} onClose={onClose} title="New case"
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={() => create.mutate()}>Create case</button></>}>
      <p className="muted small">You become the case investigator. Others see the case only when assigned.</p>
      <Field label="Reference" htmlFor="nc-ref"><input id="nc-ref" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
      <Field label="Title" htmlFor="nc-title"><input id="nc-title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
      <Field label="Location note" htmlFor="nc-loc"><input id="nc-loc" value={f.location_note} onChange={(e) => setF({ ...f, location_note: e.target.value })} /></Field>
      <label className="row"><input type="checkbox" checked={f.training} onChange={(e) => setF({ ...f, training: e.target.checked })} /> Training case</label>
      <ErrorNote error={create.error} />
    </Dialog>
  );
}

export function NoCase() {
  return <Empty>Select or create a case. Case records are visible only to assigned investigators and reviewers.</Empty>;
}
