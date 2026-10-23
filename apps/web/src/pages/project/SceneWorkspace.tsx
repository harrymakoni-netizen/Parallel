import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, sameOrigin } from "../../api/client";
import { useCases, useObservations, useOverview, useRunData, useRuns, useSceneRevision, useSceneRevisions, useScenes, useScenarios } from "../../api/hooks";
import type { Measurement, QualityView, SceneContent, SpatialObject } from "../../api/types";
import { Empty, ErrorNote, Icon, KV, ProvenanceBadge, StatusBadge, fmtNum, humanise } from "../../components/ui";
import { SceneViewer, type Marker, type PlaybackData, type ViewMode } from "../../scene/viewer";
import { useProjectId } from "./ProjectLayout";

const BELONGING: Record<string, string> = {
  recorded_scene: "Recorded scene: observed in the capture",
  prepared_model: "Prepared model: simplified analytical geometry",
  hypothetical_scenario: "Hypothetical: proposed, not observed",
};

function webglAvailable(): boolean {
  try {
    return !!document.createElement("canvas").getContext("webgl2");
  } catch {
    return false;
  }
}

export default function SceneWorkspace() {
  const pid = useProjectId();
  const { sceneId } = useParams<{ sceneId?: string }>();
  const [params, setParams] = useSearchParams();
  const scenes = useScenes(pid);
  const scene = scenes.data?.find((s) => s.id === sceneId) ?? scenes.data?.[0];
  const revs = useSceneRevisions(scene?.id);
  const revId = params.get("rev") ?? scene?.current_revision_id ?? null;
  const rev = useSceneRevision(revId);
  if (scenes.data && !scenes.data.length) {
    return <div className="page"><Empty>No scenes yet. Reconstruct a capture in <Link to="../captures">Captures</Link>, or import visual assets.</Empty></div>;
  }
  if (!rev.data) return <div className="page"><ErrorNote error={rev.error} /></div>;
  return (
    <Workspace key={rev.data.id} revId={rev.data.id} content={rev.data.content as SceneContent} quality={rev.data.quality_view} status={rev.data.status} number={rev.data.number}
      sceneName={scene?.name ?? "Scene"} sceneId={scene!.id}
      revisions={(revs.data ?? []).map((r) => ({ id: r.id, number: r.number, status: r.status }))}
      onRevision={(id) => setParams((p) => { p.set("rev", id); return p; })} />
  );
}

function Workspace({ revId, content, quality, status, number, sceneName, sceneId, revisions, onRevision }: {
  revId: string; content: SceneContent; quality?: QualityView[]; status: string; number: number; sceneName: string; sceneId: string;
  revisions: { id: string; number: number; status: string }[]; onRevision: (id: string) => void;
}) {
  const pid = useProjectId();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const ov = useOverview(pid);
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  const viewers = useRef<SceneViewer[]>([]);
  const [mode, setMode] = useState<ViewMode>("combined");
  const [tool, setTool] = useState<"select" | "measure">("select");
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [labels, setLabels] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [measure, setMeasure] = useState<{ a: number[]; b: number[]; d: number } | null>(null);
  const [visuals, setVisuals] = useState<Record<string, "loading" | "shown" | "hidden" | "error">>({});
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [notice, setNotice] = useState<string | null>(null);
  const [gridStep, setGridStep] = useState<number | null>(null);
  const runA = params.get("run");
  const runB = params.get("compare");
  const playA = useRunData<PlaybackData>(runA ?? undefined, "playback.json", !!runA);
  const playB = useRunData<PlaybackData>(runB ?? undefined, "playback.json", !!runB);
  const gl = useMemo(webglAvailable, []);
  const objects = content.spatial_objects;
  const layers = useMemo(() => Array.from(new Set(objects.map((o) => o.layer))), [objects]);
  const crash = ov.data?.project.project_type === "crashlens";
  const cases = useCases(pid);
  const observations = useObservations(crash ? cases.data?.[0]?.id : undefined);

  // viewers: one, or two side by side for a comparison
  useEffect(() => {
    if (!gl) return;
    const els = [leftRef.current, runB ? rightRef.current : null].filter(Boolean) as HTMLDivElement[];
    const vs = els.map((el) => new SceneViewer(el));
    viewers.current = vs;
    if (vs.length === 2) {
      let syncing = false;
      vs.forEach((v, i) => {
        v.onCameraChange = () => {
          if (syncing) return;
          syncing = true;
          vs[1 - i].setView(v.getView());
          syncing = false;
        };
      });
    }
    vs.forEach((v) => {
      v.onPick = setSelected;
      v.onGrid = setGridStep;
      v.onMeasure = (a, b) => setMeasure({ a: a.toArray(), b: b.toArray(), d: a.distanceTo(b) });
    });
    return () => vs.forEach((v) => v.dispose());
  }, [gl, runB]);

  useEffect(() => {
    viewers.current.forEach((v) => v.setObjects(objects, { hiddenLayers: hidden, mode, selected, showLabels: labels }));
  }, [objects, hidden, mode, selected, labels, runB]);
  useEffect(() => viewers.current.forEach((v) => { v.mode = tool; if (tool === "select") v.clearMeasure(); }), [tool]);
  useEffect(() => {
    if (!crash) return;
    const marks: Marker[] = (observations.data ?? []).filter((o) => o.position || o.geometry).map((o) => ({
      id: o.id, label: `${o.statement_type === "observation" ? "Observation" : "Interpretation"}: ${o.kind.replace(/_/g, " ")}`,
      position: o.position, geometry: o.geometry, tone: o.statement_type,
    }));
    viewers.current.forEach((v) => v.setMarkers(marks));
  }, [crash, observations.data, runB]);
  useEffect(() => {
    viewers.current[0]?.setPlayback(playA.data ?? null);
    if (viewers.current[1]) viewers.current[1].setPlayback(playB.data ?? null);
    const r = viewers.current[0]?.playbackRange;
    if (r) setT(r[0]);
  }, [playA.data, playB.data, runB]);
  useEffect(() => { viewers.current.forEach((v) => v.setTime(t)); }, [t]);
  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const r = viewers.current[0]?.playbackRange;
      setT((x) => {
        const nx = x + ((now - last) / 1000) * speed;
        if (r && nx >= r[1]) { setPlaying(false); return r[1]; }
        return nx;
      });
      last = now;
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed]);

  // visual assets: point clouds load automatically; Gaussian splats on request
  const transformFor = (frame: string): number[][] | null => {
    if (frame === "canonical") return null;
    const tr = (content.coordinates as { transforms?: { from_frame: string; to_frame: string; matrix: number[][] }[] }).transforms ?? [];
    return tr.find((x) => x.from_frame === frame && x.to_frame === "canonical")?.matrix ?? null;
  };
  const loadVisual = async (va: SceneContent["visual_assets"][number]) => {
    setVisuals((s) => ({ ...s, [va.id]: "loading" }));
    try {
      const u = await api.get<{ url: string }>(`/assets/${va.asset_id}/url`);
      const bytes = await (await fetch(sameOrigin(u.url))).arrayBuffer();
      for (const v of viewers.current) {
        if (va.kind === "point_cloud") await v.addPointCloud(va.id, bytes, transformFor(va.frame));
        else await v.addSplat(va.id, bytes, transformFor(va.frame));
      }
      setVisuals((s) => ({ ...s, [va.id]: "shown" }));
    } catch {
      setVisuals((s) => ({ ...s, [va.id]: "error" }));
    }
  };
  useEffect(() => {
    if (!gl) return;
    content.visual_assets.filter((v) => v.kind === "point_cloud").forEach((v) => void loadVisual(v));
  }, [revId, gl, runB]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveContent = useMutation({
    mutationFn: (next: SceneContent) => api.post(`/scenes/${sceneId}/revisions`, { content: next, base_revision_id: revId, message: "Workspace edit" }),
    onSuccess: (r: unknown) => { qc.invalidateQueries({ queryKey: ["scene-revs", sceneId] }); onRevision((r as { id: string }).id); },
  });
  const saveViewpoint = () => {
    const v = viewers.current[0]?.getView();
    const name = window.prompt("Name this viewpoint");
    if (!v || !name) return;
    saveContent.mutate({ ...content, viewpoints: [...content.viewpoints, { id: `vp_${Date.now()}`, name, position: v.position, target: v.target }] } as SceneContent);
  };
  const saveImage = async () => {
    const blob = await viewers.current[0]?.screenshot();
    if (!blob) return;
    const f = new FormData();
    f.append("file", new File([blob], `${sceneName.replace(/\W+/g, "-")}-view.png`, { type: "image/png" }));
    await api.upload(`/projects/${pid}/assets`, f);
    setNotice("View saved as a project image. It can be added to a report as a figure.");
    qc.invalidateQueries({ queryKey: ["assets", pid] });
  };
  const sel = objects.find((o) => o.id === selected) ?? null;
  const range = viewers.current[0]?.playbackRange ?? null;
  const runs = useRuns(pid);
  const scen = useScenarios(pid);
  const sname = new Map((scen.data ?? []).map((s) => [s.id, s.name]));
  const playable = (runs.data?.items ?? []).filter((r) => r.state === "completed" && ["jupedsim", "sumo", "virtualcrash", "isaacsim"].includes(r.engine_id));

  return (
    <div className="workspace">
      <div className="ws-toolbar" role="toolbar" aria-label="Scene tools">
        <strong>{sceneName}</strong>
        <label className="visually-hidden" htmlFor="ws-rev">Scene revision</label>
        <select id="ws-rev" value={revId} onChange={(e) => onRevision(e.target.value)}>
          {revisions.map((r) => <option key={r.id} value={r.id}>Revision {r.number} ({r.status})</option>)}
        </select>
        <span className="muted small">{status === "published" ? "Published, read-only" : "Draft"} · rev {number}</span>
        <div className="seg" role="group" aria-label="View">
          {(["captured", "model", "combined"] as ViewMode[]).map((m) => (
            <button key={m} aria-pressed={mode === m} onClick={() => setMode(m)}>{humanise(m)}</button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Tool">
          <button aria-pressed={tool === "select"} onClick={() => setTool("select")}>Select</button>
          <button aria-pressed={tool === "measure"} onClick={() => setTool("measure")}>Measure</button>
        </div>
        <button className="btn btn-small" onClick={() => viewers.current.forEach((v) => v.fit())}>Fit</button>
        <button className="btn btn-small" onClick={() => viewers.current.forEach((v) => v.topDown())}>Top-down</button>
        <label className="row small"><input type="checkbox" checked={labels} onChange={(e) => setLabels(e.target.checked)} /> Labels</label>
        {content.viewpoints.length > 0 && (
          <select aria-label="Named viewpoints" onChange={(e) => { const vp = content.viewpoints.find((x) => x.id === e.target.value); if (vp) viewers.current.forEach((v) => v.setView(vp)); }}>
            <option value="">Viewpoints…</option>
            {content.viewpoints.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
          </select>
        )}
        <button className="btn btn-small" onClick={saveViewpoint}>Save viewpoint</button>
        <button className="btn btn-small" onClick={saveImage}>Save image</button>
      </div>
      <aside className="ws-left" aria-label="Layers and objects">
        <h3>Layers</h3>
        {layers.map((l) => (
          <label key={l} className="row small">
            <input type="checkbox" checked={!hidden.has(l)} onChange={(e) => setHidden((h) => { const n = new Set(h); if (e.target.checked) n.delete(l); else n.add(l); return n; })} />
            {humanise(l)} <span className="muted">({objects.filter((o) => o.layer === l).length})</span>
          </label>
        ))}
        {content.visual_assets.length > 0 && <h3 style={{ marginTop: 12 }}>Captured visuals</h3>}
        {content.visual_assets.map((va) => (
          <div key={va.id} className="small" style={{ marginBottom: 6 }}>
            <div className="row">
              {visuals[va.id] === "shown" || visuals[va.id] === "hidden" ? (
                <label className="row"><input type="checkbox" checked={visuals[va.id] === "shown"} onChange={(e) => {
                  viewers.current.forEach((v) => v.setVisualVisible(va.id, e.target.checked));
                  setVisuals((s) => ({ ...s, [va.id]: e.target.checked ? "shown" : "hidden" }));
                }} /> {va.label}</label>
              ) : (
                <><span>{va.label}</span>{visuals[va.id] === "loading" ? <span className="spinner" aria-label="Loading" /> : <button className="btn btn-small" onClick={() => loadVisual(va)} disabled={!gl}>Load</button>}</>
              )}
            </div>
            <div className="muted">{va.kind.replace(/_/g, " ")}{visuals[va.id] === "error" ? " · could not be loaded" : ""}</div>
            {va.quality_notes.map((n) => <div key={n} className="muted">{n}</div>)}
          </div>
        ))}
        <h3 style={{ marginTop: 12 }}>Objects</h3>
        <ul className="objlist">
          {objects.filter((o) => !hidden.has(o.layer)).map((o) => (
            <li key={o.id}>
              <button aria-pressed={o.id === selected} onClick={() => setSelected(o.id)}>
                {o.name} <span className="muted">· {o.kind.replace(/_/g, " ")}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <div className={`ws-view ${runB ? "split" : ""}`}>
        {gl ? (
          <>
            <div ref={leftRef} className="ws-canvas" aria-label={`3D view of ${sceneName}. Object details are listed in the side panels.`} role="img" />
            {runB && <div ref={rightRef} className="ws-canvas" role="img" aria-label="Comparison view" />}
          </>
        ) : (
          <div className="page"><div className="note warn">3D view unavailable: this browser does not provide WebGL 2. All objects, measurements and quality states are listed in the side panels.</div></div>
        )}
        {runB && <div className="ws-split-labels"><span>{sname.get(runs.data?.items.find((r) => r.id === runA)?.scenario_id ?? "") ?? "Run A"}</span><span>{sname.get(runs.data?.items.find((r) => r.id === runB)?.scenario_id ?? "") ?? "Run B"}</span></div>}
        {gl && gridStep && <div className="ws-scale" aria-hidden="true">Grid {fmtNum(gridStep)} m</div>}
        {notice && <div className="ws-notice" role="status">{notice} <button className="btn btn-small btn-ghost" onClick={() => setNotice(null)}>Dismiss</button></div>}
      </div>
      <aside className="ws-right" aria-label="Properties">
        {sel ? <ObjectPanel o={sel} /> : <ScenePanel content={content} quality={quality} />}
        {measure && <MeasurePanel revId={revId} m={measure} />}
      </aside>
      <div className="ws-timeline" aria-label="Playback">
        <label className="visually-hidden" htmlFor="pb-run">Run to play back</label>
        <select id="pb-run" value={runA ?? ""} onChange={(e) => setParams((p) => { if (e.target.value) p.set("run", e.target.value); else p.delete("run"); return p; })}>
          <option value="">Play back a run…</option>
          {playable.map((r) => <option key={r.id} value={r.id}>{sname.get(r.scenario_id) ?? r.engine_id} · {r.label ?? r.id.slice(0, 8)}</option>)}
        </select>
        {runA && (
          <select aria-label="Compare side by side with" value={runB ?? ""} onChange={(e) => setParams((p) => { if (e.target.value) p.set("compare", e.target.value); else p.delete("compare"); return p; })}>
            <option value="">Side-by-side with…</option>
            {playable.filter((r) => r.id !== runA).map((r) => <option key={r.id} value={r.id}>{sname.get(r.scenario_id) ?? r.engine_id} · {r.label ?? r.id.slice(0, 8)}</option>)}
          </select>
        )}
        <button className="btn btn-small" onClick={() => setPlaying((p) => !p)} disabled={!range} aria-label={playing ? "Pause" : "Play"}>
          <Icon name={playing ? "pause" : "play"} /> {playing ? "Pause" : "Play"}
        </button>
        <input type="range" aria-label="Time" min={range?.[0] ?? 0} max={range?.[1] ?? 1} step={0.1} value={t} disabled={!range}
          onChange={(e) => { setPlaying(false); setT(Number(e.target.value)); }} style={{ flex: 1 }} />
        <span className="mono" aria-live="off">{fmtNum(t, 4)} s</span>
        <label className="visually-hidden" htmlFor="pb-speed">Speed</label>
        <select id="pb-speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
          {[1, 2, 4, 8, 16].map((s) => <option key={s} value={s}>{s}×</option>)}
        </select>
        {runA && <span className="badge">Engine-computed trajectory</span>}
        {playA.error ? <span className="small muted">No playback data for this run.</span> : null}
      </div>
    </div>
  );
}

function ObjectPanel({ o }: { o: SpatialObject }) {
  return (
    <div className="stack">
      <h2>{o.name}</h2>
      <KV items={[
        ["Kind", humanise(o.kind)],
        ["Layer", humanise(o.layer)],
        ["Belongs to", BELONGING[o.belongs_to] ?? o.belongs_to],
        ["Geometry", o.geometry.type],
        ["Provenance", <ProvenanceBadge key="p" kind={o.provenance.kind} />],
      ]} />
      {o.embedded_in_capture && (
        <div className="note warn">This object's appearance is embedded in the captured reconstruction. {o.captured_geometry ? "A scenario has moved it: the captured position is shown in red." : ""}</div>
      )}
      {Object.keys(o.properties).length > 0 && (
        <>
          <h3>Properties</h3>
          <table className="data">
            <tbody>
              {Object.entries(o.properties).map(([k, v]) => (
                <tr key={k}><td>{humanise(k)}</td><td>{String(v.value)} {v.unit ?? ""}</td><td><ProvenanceBadge kind={v.provenance.kind} /></td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {o.provenance.note && <p className="muted small">{o.provenance.note}</p>}
    </div>
  );
}

function ScenePanel({ content, quality }: { content: SceneContent; quality?: QualityView[] }) {
  const states: QualityView[] = quality ?? content.quality.records.map((q) => ({
    state: q.state, label: humanise(q.state), achieved: q.achieved, evidence: q.evidence ?? null, source: "recorded",
  }));
  return (
    <div className="stack">
      <h2>Scene</h2>
      <div className="row"><ProvenanceBadge kind={content.provenance.kind} />{content.hypothetical && <span className="badge badge-warning">Hypothetical</span>}</div>
      <h3>Quality states</h3>
      <p className="muted small" style={{ margin: 0 }}>Each state is assessed on its own: a reconstruction never implies the others.</p>
      <ul className="quality-list">
        {states.map((q) => (
          <li key={q.state}>
            <StatusBadge status={q.achieved ? "ok" : "unavailable"} label={q.achieved ? "Yes" : "No"} />
            <div>
              <div>{q.label}</div>
              {q.evidence && <div className="muted small">{q.evidence}</div>}
            </div>
          </li>
        ))}
        {!states.length && <li className="muted">No quality states recorded.</li>}
      </ul>
      {content.measurements.length > 0 && (
        <>
          <h3>Measurements</h3>
          <MeasurementTable ms={content.measurements} />
        </>
      )}
      {content.assumptions.length > 0 && (
        <>
          <h3>Assumptions</h3>
          <ul className="small">{content.assumptions.map((a) => <li key={a.id}>{a.statement}</li>)}</ul>
        </>
      )}
      <p className="muted small">Select an object in the view or the list to see its properties and provenance.</p>
    </div>
  );
}

/** Measurements as a compact list: the side panel is too narrow for a four-column table. */
function MeasurementTable({ ms }: { ms: Measurement[] }) {
  return (
    <ul className="measure-list">
      {ms.map((m) => (
        <li key={m.id}>
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap", alignItems: "start" }}>
            <span>{m.label}</span>
            {m.within_tolerance != null && <StatusBadge status={m.within_tolerance ? "ok" : "warning"} label={m.within_tolerance ? "Within" : "Outside"} />}
          </div>
          <div className="muted small">
            {humanise(m.purpose)} · reference {fmtNum(m.reference_distance?.value)} m · error {fmtNum(m.abs_error_m)} m
          </div>
        </li>
      ))}
    </ul>
  );
}

function MeasurePanel({ revId, m }: { revId: string; m: { a: number[]; b: number[]; d: number } }) {
  const [ref, setRef] = useState("");
  const evaluate = useMutation({
    mutationFn: () => api.post<Measurement>(`/scene-revisions/${revId}/measurements/evaluate`, {
      measurement: {
        id: "workspace", label: "Workspace measurement", purpose: "annotation", point_a: m.a, point_b: m.b, source: "picked points",
        reference_distance: ref ? { value: Number(ref), unit: "m", provenance: { kind: "measured", source: "entered in workspace" } } : null,
      },
    }),
  });
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <h3>Measurement</h3>
      <p><strong>{m.d.toFixed(3)} m</strong> between picked points</p>
      <form className="row" onSubmit={(e) => { e.preventDefault(); evaluate.mutate(); }}>
        <label className="small" htmlFor="ms-ref">Reference (m)</label>
        <input id="ms-ref" style={{ width: 90 }} inputMode="decimal" value={ref} onChange={(e) => setRef(e.target.value)} />
        <button className="btn btn-small">Check</button>
      </form>
      {evaluate.data && (
        <p className="small">Error {fmtNum(evaluate.data.abs_error_m)} m · {evaluate.data.within_tolerance == null ? "no tolerance set for this scene" : evaluate.data.within_tolerance ? "within tolerance" : "outside tolerance"}</p>
      )}
      <ErrorNote error={evaluate.error} />
      <p className="muted small">Picked points measure the reconstruction. A measurement is checked only against an independent reference.</p>
    </div>
  );
}
