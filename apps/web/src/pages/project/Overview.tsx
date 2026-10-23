import { Link } from "react-router-dom";
import { useOverview, useRuns } from "../../api/hooks";
import { Card, KV, PageHead, StatusBadge, fmtDate } from "../../components/ui";
import { PROJECT_TYPES } from "../common";
import { useProjectId } from "./ProjectLayout";

const STEPS = [
  ["Capture", "captures", "Photographs, video, Gaussian files, point clouds or terrain, hashed at upload."],
  ["Reconstruct", "captures", "COLMAP poses, scale from references, independent checks."],
  ["Prepare", "scene", "Prepared geometry and domain models from scenes or model files."],
  ["Simulate", "scenarios", "Scenarios run on established engines with recorded versions and seeds."],
  ["Compare", "compare", "Controlled comparisons with compatibility checks."],
  ["Improve", "scenarios", "Bounded optimisation over real runs; best tested candidate."],
] as const;

export function Overview() {
  const pid = useProjectId();
  const ov = useOverview(pid).data!;
  const runs = useRuns(pid);
  const p = ov.project;
  return (
    <div className="page">
      <PageHead title={p.name} intro={p.description || undefined}>
        <Link className="btn" to="scene">Open scene workspace</Link>
        <Link className="btn btn-primary" to="scenarios">Scenarios</Link>
      </PageHead>
      {p.demo_key && (
        <div className="note" style={{ marginBottom: 14 }}>
          This is a demonstration project built from <strong>synthetic</strong> material. Its results are real recorded runs,
          labelled “Saved result”. Presenter notes and source information are in <Link to="captures">Captures</Link>.
        </div>
      )}
      <div className="grid grid-4">
        {[["Scenes", ov.counts.scenes], ["Models", ov.counts.models], ["Scenarios", ov.counts.scenarios], ["Reports", ov.counts.reports]].map(([k, v]) => (
          <div key={k} className="stat"><div className="label">{k}</div><div className="value">{v}</div></div>
        ))}
      </div>
      <div className="grid grid-2" style={{ marginTop: 14 }}>
        <Card title="Workflow">
          <ol className="stack" style={{ paddingLeft: 18, margin: 0 }}>
            {STEPS.map(([name, to, text]) => (
              <li key={name}><Link to={to}><strong>{name}</strong></Link> <span className="muted">— {text}</span></li>
            ))}
          </ol>
        </Card>
        <Card title="Project">
          <KV items={[
            ["Type", PROJECT_TYPES[p.project_type]],
            ["Location", p.location_label ?? (p.location_lonlat ? p.location_lonlat.join(", ") : "Not set")],
            ["Coordinates", p.georeference ? `${String(p.georeference.horizontal_crs)} (local origin set)` : "Local metres (no georeference)"],
            ["Your role", p.my_role ?? "—"],
            ["Created", fmtDate(p.created_at)],
            ["Runs", Object.entries(ov.runs_by_state).map(([s, n]) => `${n} ${s.replace(/_/g, " ")}`).join(", ") || "None"],
          ]} />
        </Card>
      </div>
      <Card title="Recent runs" actions={<Link to="runs" className="btn btn-small">All runs</Link>}>
        <table className="data">
          <thead><tr><th>Run</th><th>Engine</th><th>State</th><th>Finished</th></tr></thead>
          <tbody>
            {runs.data?.items.slice(0, 8).map((r) => (
              <tr key={r.id}>
                <td><Link to={`runs/${r.id}`}>{r.label ?? r.id.slice(0, 8)}</Link></td>
                <td>{r.engine_id}</td>
                <td><StatusBadge status={r.state} /></td>
                <td className="small">{fmtDate(r.finished_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
