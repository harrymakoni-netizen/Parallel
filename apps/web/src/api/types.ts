/** Response shapes of the Parallel API (subset used by the web application). */

export type Json = Record<string, unknown>;

export interface User {
  id: string;
  email: string;
  display_name: string;
  is_platform_admin?: boolean;
}
export interface Me {
  user: User;
  organisations: { id: string; name: string; slug: string; role: string; settings?: Json }[];
}
export interface Organisation {
  id: string;
  name: string;
  slug: string;
  settings: Json;
  created_at: string;
}
export interface Member {
  user: User;
  role: string;
}
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
export interface Project {
  id: string;
  organisation_id: string;
  name: string;
  project_type: string;
  description: string;
  location_lonlat: [number, number] | null;
  location_label: string | null;
  georeference: Json | null;
  demo_key: string | null;
  template: string | null;
  created_at: string;
  my_role: string | null;
}
export interface Overview {
  project: Project;
  counts: Record<string, number>;
  runs_by_state: Record<string, number>;
  scenes: { id: string; name: string; current_revision_id: string | null }[];
}
export interface Asset {
  id: string;
  project_id: string;
  capture_batch_id: string | null;
  kind: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string | null;
  status: string;
  inspection: Json;
  sensitivity: string;
  created_at: string;
}
export interface CaptureBatch {
  id: string;
  name: string;
  kind: string;
  notes: string;
  synthetic: boolean;
  created_at: string;
}
export interface Revision {
  id: string;
  number: number;
  status: string;
  content: Json;
  content_sha256: string;
  message: string;
  created_at: string;
  published_at: string | null;
}
export interface Geometry {
  type: "Point" | "LineString" | "Polygon" | "MultiPolygon" | "Box";
  coordinates: unknown;
  [k: string]: unknown;
}
export interface Provenance {
  kind: string;
  source?: string | null;
  note?: string | null;
  adopted_by?: string | null;
}
export interface SpatialObject {
  id: string;
  name: string;
  kind: string;
  layer: string;
  geometry: Geometry;
  belongs_to: string;
  embedded_in_capture: boolean;
  captured_geometry?: Geometry | null;
  properties: Record<string, { value: unknown; unit: string | null; provenance: Provenance }>;
  provenance: Provenance;
}
export interface Measurement {
  id: string;
  label: string;
  purpose: string;
  point_a: number[];
  point_b: number[];
  reference_distance?: { value: number; unit: string } | null;
  reconstructed_distance_m?: number | null;
  abs_error_m?: number | null;
  within_tolerance?: boolean | null;
  source: string;
}
export interface VisualAsset {
  id: string;
  asset_id: string;
  kind: string;
  format: string;
  label: string;
  frame: string;
  quality_notes: string[];
}
export interface QualityRecord {
  state: string;
  achieved: boolean;
  evidence?: string | null;
}
export interface SceneContent {
  scene_id: string;
  hypothetical: boolean;
  visual_assets: VisualAsset[];
  spatial_objects: SpatialObject[];
  measurements: Measurement[];
  annotations: { id: string; position: number[]; title: string; body: string; kind: string }[];
  viewpoints: { id: string; name: string; position: number[]; target: number[] }[];
  quality: { records: QualityRecord[] };
  coordinates: Json;
  assumptions: { id: string; statement: string; rationale?: string }[];
  tolerance?: { abs_m?: number; rel?: number; purpose?: string } | null;
  provenance: Provenance;
}
export interface QualityView {
  state: string;
  label: string;
  achieved: boolean;
  evidence: string | null;
  /** recorded on the revision, or derived from the models, reviews and runs that cite it */
  source: "recorded" | "derived";
}
export interface SceneRevision extends Revision {
  scene_id: string;
  parent_revision_id: string | null;
  content: SceneContent & Json;
  quality_view?: QualityView[];
}
export interface DomainModel {
  id: string;
  project_id: string;
  case_id: string | null;
  name: string;
  discipline: string;
  current_revision_id: string | null;
  created_at: string;
}
export interface ModelRevision extends Revision {
  validation: { ok?: boolean; issues?: Issue[] } & Json;
  domain_model_id: string;
}
export interface Issue {
  severity: "error" | "warning" | "info";
  code?: string;
  message: string;
  path?: string | null;
}
export interface Scenario {
  id: string;
  project_id: string;
  case_id: string | null;
  domain_model_id: string;
  name: string;
  engine_id: string;
  is_baseline: boolean;
  current_revision_id: string | null;
  created_at: string;
}
export interface ScenarioRevision extends Revision {
  scenario_id: string;
  origin: string;
  adopted_by: string | null;
  adopted_at: string | null;
}
export interface Run {
  id: string;
  project_id: string;
  case_id: string | null;
  scenario_id: string;
  scenario_revision_id: string;
  domain_model_revision_id: string;
  scene_revision_id: string | null;
  engine_id: string;
  execution_mode: string;
  state: string;
  state_history: { state: string; at: string; by?: string; message?: string }[];
  label: string | null;
  seed: number | null;
  validation: { ok?: boolean; issues?: Issue[] };
  resource_estimate: Json;
  model_change_notes: string[];
  attempts: number;
  progress: number | null;
  progress_message: string | null;
  cancel_requested: boolean;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
  engine_version: string | null;
  adapter_version: string | null;
  input_hashes: Record<string, string>;
  result_summary: Json;
  warnings: string[];
  imported_at: string | null;
  external_installation: string | null;
  created_at: string;
}
export interface Metric {
  metric_id: string;
  entity: string | null;
  value: number | null;
  unit: string;
  status: string;
  note: string | null;
  stats: Record<string, number> | null;
}
export interface MetricDef {
  id: string;
  name: string;
  unit: string;
  definition: string;
  calculation: string;
  source_outputs: string[];
  extent: string;
  time_interval: string;
  missing_value_rule: string;
  discipline: string;
  per_entity: boolean;
}
export interface Artifact {
  id: string;
  name: string;
  kind: string;
  media_type: string;
  sha256: string;
  size_bytes: number;
  description: string | null;
  created_at: string;
}
export interface EngineEntry {
  capability: {
    engine_id: string;
    name: string;
    vendor: string;
    role: string;
    disciplines: string[];
    dimensions: string[];
    execution_modes: string[];
    limitations: string[];
    tested_versions: { version: string; platform: string; verified_on?: string | null; status: string }[];
    licence: { kind: string; name: string; requires_key: boolean; note?: string | null };
    result_types: string[];
    official_docs: string[];
    adapter_version: string;
    companion_tools: string[];
  };
  default_mode: string;
  availability: {
    execution_mode: string;
    mode_label: string;
    availability: string;
    availability_label: string;
    detected_version?: string | null;
    details: string[];
    remedy?: string | null;
  }[];
}
export interface ComparisonCell {
  value: number | null;
  status: string;
  note?: string | null;
  stats?: Record<string, number> | null;
  abs_diff?: number | null;
  pct_diff?: number | null;
  diff_note?: string | null;
}
export interface ComparisonRow {
  metric_id: string;
  entity: string | null;
  unit: string | null;
  baseline: ComparisonCell;
  candidates: Record<string, ComparisonCell>;
}
export interface Comparison {
  id: string;
  project_id: string;
  name: string;
  baseline_run_id: string;
  run_ids: string[];
  compatibility: {
    controlled: boolean;
    summary: string;
    checks: { check: string; status: string; detail: string }[];
    runs: { run_id: string; label: string; engine: string; seed: number | null; model_change_notes: string[] }[];
    candidate_labels?: Record<string, string>;
  };
  table: ComparisonRow[];
  created_at: string;
}
export interface Report {
  id: string;
  project_id: string;
  kind: string;
  title: string;
  status: string;
  run_ids: string[];
  comparison_id: string | null;
  case_id: string | null;
  review_status: string;
  manifest: Json;
  error: string | null;
  created_at: string;
  generated_at: string | null;
}
export interface Case {
  id: string;
  project_id: string;
  reference: string;
  title: string;
  status: string;
  incident_at: string | null;
  location_note: string;
  training: boolean;
  created_at: string;
  my_role: string | null;
}
export interface Evidence {
  id: string;
  case_id: string;
  source_asset_id: string | null;
  kind: string;
  title: string;
  description: string;
  claimed_captured_at: string | null;
  captured_by: string | null;
  source_metadata: Json;
  original_sha256: string | null;
  created_at: string;
  integrity_note: string;
}
export interface Observation {
  id: string;
  case_id: string;
  statement_type: "observation" | "interpretation";
  kind: string;
  text: string;
  evidence_item_id: string | null;
  based_on: string[];
  position: number[] | null;
  geometry: Geometry | null;
  measurement_value: number | null;
  measurement_unit: string | null;
  uncertainty: Json | null;
  limitation: string;
  review_status: string;
  author_id: string | null;
  created_at: string;
}
export interface Hypothesis {
  id: string;
  case_id: string;
  label: string;
  status: string;
  content: {
    description: string;
    participant_ids: string[];
    sequence?: { order: number; description: string }[];
    unresolved_questions?: string[];
    analyst_interpretation?: string;
    evidence_fit?: Json;
  };
  links: { id: string; observation_id: string; relation: string; rationale: string }[];
  created_at: string;
}
export interface TimelineEvent {
  id: string;
  t_offset_s: number | null;
  at: string | null;
  t_uncertainty_s: number | null;
  description: string;
  source_type: string;
  source_id: string | null;
  hypothesis_id: string | null;
}
export interface Vehicle {
  id: string;
  label: string;
  make: string | null;
  model: string | null;
  mass_kg: number | null;
  length_m: number | null;
  width_m: number | null;
  data_source: string;
}
export interface ReconJob {
  id: string;
  status: string;
  stages: { name: string; status: string; message: string | null; data?: Json; started_at?: string; finished_at?: string }[];
  settings: Json;
  error: string | null;
  result_scene_revision_id: string | null;
  created_at: string;
}
export interface Optimisation {
  id: string;
  project_id: string;
  name: string;
  status: string;
  definition: Json;
  candidates: {
    index: number;
    values: Record<string, unknown>;
    run_id?: string;
    run_state?: string;
    objective: number | null;
    objective_status: string;
    feasible: boolean;
    constraints: { metric_id: string; op: string; value: number; observed: number | null; status: string }[];
    metrics?: Record<string, number | null>;
  }[];
  best_candidate_index: number | null;
  best_label: string | null;
  stop_reason: string | null;
  presentation_note: string;
  created_at: string;
}
export interface AssistantItem {
  seq: number;
  type: "user" | "assistant" | "notice" | "tool_call";
  text?: string;
  kind?: string;
  tool?: string;
  id?: string;
  arguments?: Json;
  result?: Json;
  authorised?: boolean;
  error?: string | null;
  suggestion?: boolean;
  adopted_by?: string | null;
  served_by?: string;
  fallback?: boolean;
}
export interface Activity {
  id: string;
  action: string;
  object_type: string;
  object_id: string | null;
  actor: string | null;
  details: Json;
  created_at: string;
}
