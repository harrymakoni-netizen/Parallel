import { Link } from "react-router-dom";
import { useProjects } from "../api/hooks";
import { Empty, ErrorNote, Loading, PageHead } from "../components/ui";
import { PROJECT_TYPES } from "./common";

const REQUIRED: Record<string, string> = {
  mine_evacuation: "Baseline, blocked route and proposed alternative.",
  crashlens: "Captured scene, evidence annotations and alternative hypotheses.",
  construction: "Existing layout, operational alternative and evacuation scenario.",
  urban_planning: "Baseline transport arrangement and proposed change.",
  urban_drainage: "Existing network, constrained condition and proposed intervention.",
};

export function Demonstrations() {
  const q = useProjects();
  const demos = q.data?.items.filter((p) => p.demo_key) ?? [];
  return (
    <div className="page">
      <PageHead title="Demonstrations" intro={<>Five prepared projects built from clearly identified <strong>synthetic</strong> material. Their results come from real recorded engine runs, labelled as saved results.</>} />
      {q.isLoading && <Loading />}
      <ErrorNote error={q.error} />
      {q.data && !demos.length && (
        <Empty>
          No demonstration projects are installed. An operator can create them with <code>python -m parallel_api.demo seed</code>.
        </Empty>
      )}
      <div className="grid grid-2">
        {demos.map((p) => (
          <Link key={p.id} to={`/p/${p.id}`} className="card" style={{ textDecoration: "none", color: "inherit" }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="badge badge-accent">{PROJECT_TYPES[p.project_type]}</span>
              <span className="badge badge-synthetic">Synthetic</span>
            </div>
            <h2 style={{ marginTop: 10 }}>{p.name}</h2>
            <p className="muted" style={{ marginTop: 6 }}>{p.description}</p>
            <p className="small" style={{ marginTop: 10 }}><strong>Comparison:</strong> {REQUIRED[p.demo_key!]}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}
