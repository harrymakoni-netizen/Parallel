import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useHypotheses, useObservations } from "../../api/hooks";
import type { Case } from "../../api/types";
import { Card, ErrorNote, PageHead, StatusBadge, fmtDate, humanise } from "../../components/ui";
import { ReviewDialog } from "../project/ProjectReports";
import { useProjectId } from "../project/ProjectLayout";
import { CaseBar, NoCase, useSelectedCase } from "./CaseSelect";

export function CaseReview() {
  const { current } = useSelectedCase();
  return (
    <div className="page">
      <PageHead title="Case Review" intro="Case reviewers record scoped reviews of evidence annotations, interpretations and hypotheses. A review states exactly what was reviewed and implies nothing broader." />
      <CaseBar />
      {current ? <Review c={current} /> : <NoCase />}
    </div>
  );
}

function Review({ c }: { c: Case }) {
  const pid = useProjectId();
  const nav = useNavigate();
  const qc = useQueryClient();
  const obs = useObservations(c.id);
  const hyps = useHypotheses(c.id);
  const reviews = useQuery({ queryKey: ["case-reviews", c.id], queryFn: () => api.get<{ id: string; object_type: string; object_id: string; scope: string; status: string; comment: string; created_at: string }[]>(`/cases/${c.id}/reviews`) });
  const [target, setTarget] = useState<{ type: string; id: string; scopes: string[] } | null>(null);
  const canReview = c.my_role === "reviewer";
  const report = useMutation({
    mutationFn: () => api.post(`/projects/${pid}/reports`, { kind: "case", title: `${c.reference} case report`, case_id: c.id }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reports", pid] }); nav(`/p/${pid}/reports`); },
  });
  return (
    <div className="stack">
      <Card title="Statements and hypotheses" actions={c.my_role === "investigator" && <button className="btn btn-small btn-primary" onClick={() => report.mutate()}>Generate case report</button>}>
        <ErrorNote error={report.error} />
        <table className="data">
          <thead><tr><th>Item</th><th>Type</th><th>Review status</th><th /></tr></thead>
          <tbody>
            {obs.data?.map((o) => (
              <tr key={o.id}>
                <td>{o.text}</td><td>{humanise(o.statement_type)}</td>
                <td><StatusBadge status={o.review_status === "unreviewed" ? "info" : o.review_status} label={o.review_status} /></td>
                <td>{canReview && <button className="btn btn-small" onClick={() => setTarget({ type: "evidence_observation", id: o.id, scopes: o.statement_type === "observation" ? ["evidence_annotations"] : ["interpretation"] })}>Review</button>}</td>
              </tr>
            ))}
            {hyps.data?.map((h) => (
              <tr key={h.id}>
                <td>{h.label}</td><td>Hypothesis</td><td>—</td>
                <td>{canReview && <button className="btn btn-small" onClick={() => setTarget({ type: "hypothesis", id: h.id, scopes: ["interpretation", "model_assumptions"] })}>Review</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!canReview && <p className="muted small" style={{ marginTop: 8 }}>Only case reviewers can record reviews.</p>}
      </Card>
      <Card title="Review records">
        <table className="data">
          <thead><tr><th>When</th><th>Object</th><th>Scope</th><th>Outcome</th><th>Comment</th></tr></thead>
          <tbody>{reviews.data?.map((r) => <tr key={r.id}><td className="small">{fmtDate(r.created_at)}</td><td>{humanise(r.object_type)}</td><td>{humanise(r.scope)}</td><td><StatusBadge status={r.status === "approved" ? "ok" : "warning"} label={humanise(r.status)} /></td><td className="small">{r.comment}</td></tr>)}</tbody>
        </table>
      </Card>
      {target && <ReviewDialog open onClose={() => { setTarget(null); qc.invalidateQueries({ queryKey: ["case-reviews", c.id] }); }} objectType={target.type} objectId={target.id} scopes={target.scopes} />}
    </div>
  );
}
