import { useQueries } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useProjects } from "../api/hooks";
import type { Report } from "../api/types";
import { Empty, Loading, PageHead, StatusBadge, fmtDate } from "../components/ui";

export function AllReports() {
  const projects = useProjects();
  const lists = useQueries({
    queries: (projects.data?.items ?? []).map((p) => ({ queryKey: ["reports", p.id], queryFn: () => api.get<Report[]>(`/projects/${p.id}/reports`) })),
  });
  const byProject = new Map((projects.data?.items ?? []).map((p) => [p.id, p]));
  const reports = lists.flatMap((l) => l.data ?? []).sort((a, b) => b.created_at.localeCompare(a.created_at));
  return (
    <div className="page">
      <PageHead title="Reports" intro="Reports from every project you can access. Each report links its claims to the runs, revisions and engine versions behind them." />
      {(projects.isLoading || lists.some((l) => l.isLoading)) && <Loading />}
      {!reports.length && projects.data && <Empty>No reports yet.</Empty>}
      {reports.length > 0 && (
        <div className="table-wrap card">
          <table className="data">
            <thead><tr><th>Title</th><th>Project</th><th>Kind</th><th>Status</th><th>Review</th><th>Generated</th></tr></thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.id}>
                  <td><Link to={`/p/${r.project_id}/reports`}>{r.title}</Link></td>
                  <td>{byProject.get(r.project_id)?.name}</td>
                  <td>{r.kind}</td>
                  <td><StatusBadge status={r.status} /></td>
                  <td className="small">{r.review_status}</td>
                  <td className="small">{fmtDate(r.generated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
