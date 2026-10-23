import { useActivity } from "../../api/hooks";
import { Card, ErrorNote, Loading, PageHead, fmtDate, humanise } from "../../components/ui";
import { useProjectId } from "./ProjectLayout";

export function Activity() {
  const pid = useProjectId();
  const q = useActivity(pid);
  return (
    <div className="page">
      <PageHead title="Activity" intro="Consequential changes in this project, newest first. Case activity is visible only to people assigned to the case." />
      {q.isLoading && <Loading />}
      <ErrorNote error={q.error} />
      <Card>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Object</th></tr></thead>
            <tbody>
              {q.data?.items.map((a) => (
                <tr key={a.id}>
                  <td className="small">{fmtDate(a.created_at)}</td>
                  <td>{a.actor ?? "System"}</td>
                  <td>{humanise(a.action)}</td>
                  <td className="small mono">{a.object_type} {a.object_id?.slice(0, 8)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
