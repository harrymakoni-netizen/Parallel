import { lazy, Suspense, useState } from "react";
import { NavLink, Route, Routes, useParams } from "react-router-dom";
import { useOverview } from "../../api/hooks";
import { ErrorNote, Loading } from "../../components/ui";
import { PROJECT_TYPES } from "../common";
import { Activity } from "./Activity";
import { Captures } from "./Captures";
import { Compare } from "./Compare";
import { Models } from "./Models";
import { Overview } from "./Overview";
import { ProjectReports } from "./ProjectReports";
import { RunDetail } from "./RunDetail";
import { Runs } from "./Runs";
import { Scenarios } from "./Scenarios";
import { CaseReview } from "../crash/CaseReview";
import { EvidencePage } from "../crash/Evidence";
import { Hypotheses } from "../crash/Hypotheses";
import { Timeline } from "../crash/Timeline";

const SceneWorkspace = lazy(() => import("./SceneWorkspace"));
const AssistantPanel = lazy(() => import("./AssistantPanel"));

export const useProjectId = () => useParams<{ pid: string }>().pid!;

export function ProjectLayout() {
  const pid = useProjectId();
  const ov = useOverview(pid);
  const [assistant, setAssistant] = useState(false);
  if (ov.isLoading) return <div className="page"><Loading /></div>;
  if (ov.error || !ov.data) return <div className="page"><ErrorNote error={ov.error ?? "Project not found"} /></div>;
  const p = ov.data.project;
  const crash = p.project_type === "crashlens";
  const base = `/p/${pid}`;
  return (
    <div className="layout">
      <nav className="sidenav" aria-label="Project">
        <div className="proj-name">{p.name}</div>
        <div className="proj-type">
          {PROJECT_TYPES[p.project_type]}
          {p.demo_key && <span className="badge badge-synthetic" style={{ marginLeft: 6 }}>Synthetic demo</span>}
        </div>
        <NavLink end to={base}>Overview</NavLink>
        <NavLink to={`${base}/captures`}>Captures</NavLink>
        <NavLink to={`${base}/scene`}>Scene Workspace</NavLink>
        <NavLink to={`${base}/models`}>Models</NavLink>
        <NavLink to={`${base}/scenarios`}>Scenarios</NavLink>
        <NavLink to={`${base}/runs`}>Runs</NavLink>
        <NavLink to={`${base}/compare`}>Compare</NavLink>
        <NavLink to={`${base}/reports`}>Reports</NavLink>
        <NavLink to={`${base}/activity`}>Activity</NavLink>
        {crash && (
          <>
            <div className="section">CrashLens</div>
            <NavLink to={`${base}/evidence`}>Evidence</NavLink>
            <NavLink to={`${base}/timeline`}>Timeline</NavLink>
            <NavLink to={`${base}/hypotheses`}>Hypotheses</NavLink>
            <NavLink to={`${base}/review`}>Case Review</NavLink>
          </>
        )}
        <div className="spacer" />
        <button className="btn btn-small" onClick={() => setAssistant((a) => !a)} aria-expanded={assistant} aria-controls="assistant-panel">
          {assistant ? "Close assistant" : "Assistant"}
        </button>
      </nav>
      <div className={`project-main${assistant ? " with-assistant" : ""}`}>
        <Suspense fallback={<div className="page"><Loading /></div>}>
          <Routes>
            <Route index element={<Overview />} />
            <Route path="captures" element={<Captures />} />
            <Route path="scene" element={<SceneWorkspace />} />
            <Route path="scene/:sceneId" element={<SceneWorkspace />} />
            <Route path="models" element={<Models />} />
            <Route path="scenarios" element={<Scenarios />} />
            <Route path="runs" element={<Runs />} />
            <Route path="runs/:rid" element={<RunDetail />} />
            <Route path="compare" element={<Compare />} />
            <Route path="reports" element={<ProjectReports />} />
            <Route path="activity" element={<Activity />} />
            <Route path="evidence" element={<EvidencePage />} />
            <Route path="timeline" element={<Timeline />} />
            <Route path="hypotheses" element={<Hypotheses />} />
            <Route path="review" element={<CaseReview />} />
          </Routes>
          {assistant && <AssistantPanel onClose={() => setAssistant(false)} />}
        </Suspense>
      </div>
    </div>
  );
}
