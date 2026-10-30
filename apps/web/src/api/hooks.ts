/** TanStack Query hooks for the Parallel API. */
import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type {
  Activity, Artifact, Asset, AuthConfig, CaptureBatch, Case, Comparison, DomainModel, EngineEntry, Evidence, Hypothesis, Me, Metric,
  MetricDef, ModelRevision, Observation, Optimisation, Overview, Page, Project, ReconJob, Report, Run, Scenario,
  ScenarioRevision, SceneRevision, TimelineEvent, Vehicle,
} from "./types";

export const useAuthConfig = () => useQuery({ queryKey: ["auth-config"], staleTime: Infinity, retry: false, queryFn: () => api.get<AuthConfig>("/auth/config") });
export const useMe = (enabled = true) => useQuery({ queryKey: ["me"], enabled, queryFn: () => api.get<Me>("/auth/me"), retry: false });
export const useProjects = () => useQuery({ queryKey: ["projects"], queryFn: () => api.get<Page<Project>>("/projects?limit=200") });
export const useOverview = (pid: string) => useQuery({ queryKey: ["overview", pid], queryFn: () => api.get<Overview>(`/projects/${pid}/overview`) });
export const useTemplates = () => useQuery({ queryKey: ["templates"], queryFn: () => api.get<{ key: string; name: string }[]>("/projects/templates") });
export const useAssets = (pid: string) => useQuery({ queryKey: ["assets", pid], queryFn: () => api.get<Page<Asset>>(`/projects/${pid}/assets?limit=200`) });
export const useBatches = (pid: string) => useQuery({ queryKey: ["batches", pid], queryFn: () => api.get<CaptureBatch[]>(`/projects/${pid}/capture-batches`) });
export const useRecons = (pid: string) => useQuery({ queryKey: ["recons", pid], queryFn: () => api.get<ReconJob[]>(`/projects/${pid}/reconstructions`) });
export const useScenes = (pid: string) => useQuery({ queryKey: ["scenes", pid], queryFn: () => api.get<{ id: string; name: string; current_revision_id: string | null }[]>(`/projects/${pid}/scenes`) });
export const useSceneRevisions = (sid: string | undefined) => useQuery({ queryKey: ["scene-revs", sid], enabled: !!sid, queryFn: () => api.get<SceneRevision[]>(`/scenes/${sid}/revisions`) });
export const useSceneRevision = (rid: string | null | undefined) => useQuery({ queryKey: ["scene-rev", rid], enabled: !!rid, queryFn: () => api.get<SceneRevision>(`/scene-revisions/${rid}`) });
export const useModels = (pid: string) => useQuery({ queryKey: ["models", pid], queryFn: () => api.get<DomainModel[]>(`/projects/${pid}/models`) });
export const useModelRevision = (rid: string | null | undefined) => useQuery({ queryKey: ["model-rev", rid], enabled: !!rid, queryFn: () => api.get<ModelRevision>(`/model-revisions/${rid}`) });
export const useScenarios = (pid: string) => useQuery({ queryKey: ["scenarios", pid], queryFn: () => api.get<Scenario[]>(`/projects/${pid}/scenarios`) });
export const useScenarioRevision = (rid: string | null | undefined) => useQuery({ queryKey: ["scenario-rev", rid], enabled: !!rid, queryFn: () => api.get<ScenarioRevision>(`/scenario-revisions/${rid}`) });
export const useRuns = (pid: string) => useQuery({ queryKey: ["runs", pid], queryFn: () => api.get<Page<Run>>(`/projects/${pid}/runs?limit=200`), refetchInterval: (q) => (q.state.data?.items.some((r) => !["completed", "failed", "cancelled", "awaiting_external_result"].includes(r.state)) ? 3000 : false) });
export const useRun = (rid: string | undefined) => useQuery({ queryKey: ["run", rid], enabled: !!rid, queryFn: () => api.get<Run>(`/runs/${rid}`) });
export const useMetrics = (rid: string | undefined) => useQuery({ queryKey: ["metrics", rid], enabled: !!rid, queryFn: () => api.get<Metric[]>(`/runs/${rid}/metrics`) });
export const useArtifacts = (rid: string | undefined) => useQuery({ queryKey: ["artifacts", rid], enabled: !!rid, queryFn: () => api.get<Artifact[]>(`/runs/${rid}/artifacts`) });
export const useRunData = <T,>(rid: string | undefined, name: string, enabled = true) => useQuery({ queryKey: ["run-data", rid, name], enabled: !!rid && enabled, retry: false, queryFn: () => api.get<T>(`/runs/${rid}/data/${name}`) });
export const useMetricDefs = () => useQuery({ queryKey: ["metric-defs"], staleTime: Infinity, queryFn: () => api.get<MetricDef[]>("/metrics") });
export const useEngines = (orgId: string | undefined) => useQuery({ queryKey: ["engines", orgId], enabled: !!orgId, queryFn: () => api.get<EngineEntry[]>(`/engines?organisation_id=${orgId}`) });
export const useComparisons = (pid: string) => useQuery({ queryKey: ["comparisons", pid], queryFn: () => api.get<Comparison[]>(`/projects/${pid}/comparisons`) });
export const useReports = (pid: string) => useQuery({ queryKey: ["reports", pid], queryFn: () => api.get<Report[]>(`/projects/${pid}/reports`) });
export const useActivity = (pid: string) => useQuery({ queryKey: ["activity", pid], queryFn: () => api.get<{ items: Activity[]; total: number }>(`/projects/${pid}/activity?limit=100`) });
export const useOptimisations = (pid: string) => useQuery({ queryKey: ["optimisations", pid], queryFn: () => api.get<Optimisation[]>(`/projects/${pid}/optimisations`) });
export const useCases = (pid: string) => useQuery({ queryKey: ["cases", pid], queryFn: () => api.get<Case[]>(`/projects/${pid}/cases`) });
export const useEvidence = (cid: string | undefined) => useQuery({ queryKey: ["evidence", cid], enabled: !!cid, queryFn: () => api.get<Evidence[]>(`/cases/${cid}/evidence`) });
export const useObservations = (cid: string | undefined) => useQuery({ queryKey: ["observations", cid], enabled: !!cid, queryFn: () => api.get<Observation[]>(`/cases/${cid}/observations`) });
export const useHypotheses = (cid: string | undefined) => useQuery({ queryKey: ["hypotheses", cid], enabled: !!cid, queryFn: () => api.get<Hypothesis[]>(`/cases/${cid}/hypotheses`) });
export const useTimeline = (cid: string | undefined) => useQuery({ queryKey: ["timeline", cid], enabled: !!cid, queryFn: () => api.get<TimelineEvent[]>(`/cases/${cid}/timeline`) });
export const useVehicles = (cid: string | undefined) => useQuery({ queryKey: ["vehicles", cid], enabled: !!cid, queryFn: () => api.get<Vehicle[]>(`/cases/${cid}/vehicles`) });
