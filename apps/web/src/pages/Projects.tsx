import { Link } from "react-router-dom";
import { useProjects } from "../api/hooks";
import { Empty, ErrorNote, Loading, PageHead, fmtDate } from "../components/ui";
import { PROJECT_TYPES } from "./common";

export function Projects() {
  const q = useProjects();
  const items = q.data?.items.filter((p) => !p.demo_key) ?? [];
  return (
    <div className="page">
      <PageHead title="Projects" intro="Each project holds captures, scenes, prepared models, scenarios and the runs that compare them.">
        <Link className="btn btn-primary" to="/projects/new">New project</Link>
      </PageHead>
      {q.isLoading && <Loading />}
      <ErrorNote error={q.error} />
      {q.data && !items.length && (
        <Empty>
          No projects yet. <Link to="/projects/new">Create a project</Link> or open a{" "}
          <Link to="/demonstrations">demonstration</Link>.
        </Empty>
      )}
      <div className="stack">
        {items.map((p) => (
          <Link key={p.id} to={`/p/${p.id}`} className="list-item">
            <div style={{ flex: 1 }}>
              <strong>{p.name}</strong>
              <div className="muted small">{PROJECT_TYPES[p.project_type] ?? p.project_type} · {p.location_label ?? "No location"} · created {fmtDate(p.created_at)}</div>
            </div>
            <span className="badge">{p.my_role ?? "member"}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
