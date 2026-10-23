import { Link } from "react-router-dom";
import { useRuns, useScenarios } from "../../api/hooks";
import { Card, Empty, ErrorNote, Loading, PageHead, StatusBadge, fmtDate } from "../../components/ui";
import { useProjectId } from "./ProjectLayout";

export function Runs() {
  const pid = useProjectId();
  const runs = useRuns(pid);
  const scenarios = useScenarios(pid);
  const name = new Map((scenarios.data ?? []).map((s) => [s.id, s.name]));
  return (
    <div className="page">
      <PageHead title="Runs" intro="Every run records the exact scenario, model and scene revisions, engine and adapter versions, seed and input hashes. Submit runs from Scenarios." />
      {runs.isLoading && <Loading />}
      <ErrorNote error={runs.error} />
      {runs.data && !runs.data.items.length && <Empty>No runs yet. Open <Link to="../scenarios">Scenarios</Link> to run one.</Empty>}
      {!!runs.data?.items.length && (
        <Card>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Run</th><th>Scenario</th><th>Engine</th><th>Mode</th><th>State</th><th>Progress</th><th>Finished</th></tr></thead>
              <tbody>
                {runs.data.items.map((r) => (
                  <tr key={r.id}>
                    <td><Link to={r.id}>{r.label ?? r.id.slice(0, 8)}</Link><div className="muted mono">{r.id.slice(0, 12)}</div></td>
                    <td>{name.get(r.scenario_id) ?? "—"}</td>
                    <td>{r.engine_id} <span className="muted small">{r.engine_version ?? ""}</span></td>
                    <td className="small">{r.execution_mode.replace("_", " ")}</td>
                    <td><StatusBadge status={r.state} /></td>
                    <td style={{ minWidth: 120 }}>
                      {r.progress !== null && r.state !== "completed" ? (
                        <div className="progress" role="progressbar" aria-valuenow={Math.round((r.progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Run progress">
                          <span style={{ width: `${Math.round((r.progress ?? 0) * 100)}%` }} />
                        </div>
                      ) : <span className="muted small">—</span>}
                    </td>
                    <td className="small">{fmtDate(r.finished_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
