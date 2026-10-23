import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, sameOrigin } from "../../api/client";
import { useOverview, useReports, useRuns, useScenarios } from "../../api/hooks";
import type { Report } from "../../api/types";
import { Card, Dialog, Empty, ErrorNote, Field, PageHead, StatusBadge, fmtDate } from "../../components/ui";
import { useProjectId } from "./ProjectLayout";

export function ProjectReports() {
  const pid = useProjectId();
  const reports = useReports(pid);
  const role = useOverview(pid).data?.project.my_role;
  const [creating, setCreating] = useState(false);
  return (
    <div className="page">
      <PageHead title="Reports" intro="PDF, metrics CSV and JSON manifest generated from the same manifest. Every claim carries its run identifier; original evidence media is never embedded.">
        {role && role !== "viewer" && role !== "reviewer" && <button className="btn btn-primary" onClick={() => setCreating(true)}>New report</button>}
      </PageHead>
      {!reports.data?.length && <Empty>No reports yet. Create one here, or from a comparison.</Empty>}
      <div className="stack">
        {reports.data?.map((r) => <ReportRow key={r.id} r={r} canReview={role === "reviewer" || role === "org_admin"} />)}
      </div>
      <NewReport open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}

function ReportRow({ r, canReview }: { r: Report; canReview: boolean }) {
  const qc = useQueryClient();
  const [reviewing, setReviewing] = useState(false);
  const get = useMutation({
    mutationFn: async (name: string) => {
      const u = await api.get<{ url: string }>(`/reports/${r.id}/files/${name}`);
      window.location.assign(sameOrigin(u.url));
    },
  });
  const regen = useMutation({ mutationFn: () => api.post(`/reports/${r.id}/regenerate`), onSuccess: () => qc.invalidateQueries({ queryKey: ["reports"] }) });
  const files = (r.manifest.files ?? {}) as Record<string, { sha256: string; size_bytes: number }>;
  return (
    <Card title={r.title} intro={`${r.kind} report · ${fmtDate(r.generated_at ?? r.created_at)} · ${r.run_ids.length} runs`}
      actions={<StatusBadge status={r.status} />}>
      <div className="row">
        <span className="small"><strong>Review:</strong> {r.review_status}</span>
        <div className="spacer" />
        {r.status === "completed" && ["report.pdf", "metrics.csv", "manifest.json"].map((n) => (
          <button key={n} className="btn btn-small" onClick={() => get.mutate(n)} title={files[n] ? `SHA-256 ${files[n].sha256}` : undefined}>{n}</button>
        ))}
        {(r.status === "failed" || r.status === "completed") && <button className="btn btn-small btn-ghost" onClick={() => regen.mutate()}>Regenerate</button>}
        {canReview && <button className="btn btn-small" onClick={() => setReviewing(true)}>Record review</button>}
      </div>
      {r.error && <ErrorNote error={r.error} />}
      <ErrorNote error={get.error ?? regen.error} />
      <ReviewDialog open={reviewing} onClose={() => setReviewing(false)} objectType="report" objectId={r.id} scopes={["report"]} />
    </Card>
  );
}

export function ReviewDialog({ open, onClose, objectType, objectId, scopes }: {
  open: boolean; onClose: () => void; objectType: string; objectId: string; scopes: string[];
}) {
  const qc = useQueryClient();
  const [scope, setScope] = useState(scopes[0]);
  const [status, setStatus] = useState("approved");
  const [comment, setComment] = useState("");
  const save = useMutation({
    mutationFn: () => api.post("/reviews", { object_type: objectType, object_id: objectId, scope, status, comment }),
    onSuccess: () => { qc.invalidateQueries(); onClose(); },
  });
  return (
    <Dialog open={open} onClose={onClose} title="Record a scoped review"
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>Save review</button></>}>
      <p className="muted small">A review states exactly what was reviewed. It does not certify anything beyond that scope.</p>
      <Field label="Scope" htmlFor="rv-scope">
        <select id="rv-scope" value={scope} onChange={(e) => setScope(e.target.value)}>
          {scopes.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
        </select>
      </Field>
      <Field label="Outcome" htmlFor="rv-status">
        <select id="rv-status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="approved">Approved</option><option value="changes_requested">Changes requested</option><option value="commented">Commented</option>
        </select>
      </Field>
      <Field label="Comment" htmlFor="rv-comment"><textarea id="rv-comment" value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
      <ErrorNote error={save.error} />
    </Dialog>
  );
}

function NewReport({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pid = useProjectId();
  const qc = useQueryClient();
  const runs = useRuns(pid);
  const scen = useScenarios(pid);
  const sname = new Map((scen.data ?? []).map((s) => [s.id, s.name]));
  const [title, setTitle] = useState("");
  const [ids, setIds] = useState<string[]>([]);
  const [narrative, setNarrative] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<Report>(`/projects/${pid}/reports`, {
      kind: "project", title, run_ids: ids, narrative: narrative || null, narrative_origin: narrative ? "manual" : null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reports", pid] }); onClose(); },
  });
  return (
    <Dialog open={open} onClose={onClose} title="New project report"
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={() => create.mutate()} disabled={!title || !ids.length || create.isPending}>{create.isPending ? "Generating…" : "Generate report"}</button></>}>
      <Field label="Title" htmlFor="nr-title"><input id="nr-title" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }} className="stack">
        <legend style={{ fontWeight: 560, fontSize: 13 }}>Completed runs</legend>
        {runs.data?.items.filter((r) => r.state === "completed" && !r.case_id).map((r) => (
          <label key={r.id} className="row small">
            <input type="checkbox" checked={ids.includes(r.id)} onChange={(e) => setIds(e.target.checked ? [...ids, r.id] : ids.filter((x) => x !== r.id))} />
            {sname.get(r.scenario_id)} · {r.label ?? r.id.slice(0, 8)}
          </label>
        ))}
      </fieldset>
      <Field label="Narrative (optional)" htmlFor="nr-narr" hint="Written by you; cite run and metric identifiers for any number you state.">
        <textarea id="nr-narr" value={narrative} onChange={(e) => setNarrative(e.target.value)} />
      </Field>
      <ErrorNote error={create.error} />
    </Dialog>
  );
}
