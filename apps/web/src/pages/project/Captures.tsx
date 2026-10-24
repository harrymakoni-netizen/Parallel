import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, sameOrigin, streamEvents } from "../../api/client";
import { useAssets, useBatches, useRecons } from "../../api/hooks";
import type { Asset, ReconJob } from "../../api/types";
import { Card, Dialog, ErrorNote, Field, PageHead, StatusBadge, fmtBytes, fmtDate, shortId } from "../../components/ui";
import { useProjectId } from "./ProjectLayout";

export function Captures() {
  const pid = useProjectId();
  const batches = useBatches(pid);
  const assets = useAssets(pid);
  const recons = useRecons(pid);
  const docs = (assets.data?.items ?? []).filter((a) => !a.capture_batch_id && a.kind === "document");
  return (
    <div className="page">
      <PageHead title="Captures" intro="Uploads are verified by type and content, hashed (SHA-256) and stored as originals. Reconstruction learns a visual representation; it is never treated as physical validation." />
      <Guidance />
      {docs.length > 0 && (
        <Card title="Project documents">
          <div className="row">{docs.map((d) => <DocButton key={d.id} asset={d} />)}</div>
        </Card>
      )}
      <NewBatch />
      {batches.data?.map((b) => (
        <Batch key={b.id} batch={b} assets={(assets.data?.items ?? []).filter((a) => a.capture_batch_id === b.id)} />
      ))}
      <Card title="Reconstruction jobs">
        {!recons.data?.length && <p className="muted">No reconstructions yet. Start one from a photo or video batch.</p>}
        <div className="stack">{recons.data?.map((j) => <ReconRow key={j.id} job={j} />)}</div>
      </Card>
    </div>
  );
}

function Guidance() {
  const g = useQuery({ queryKey: ["guidance"], queryFn: () => api.get<{ title: string; sections: { heading: string; points: string[] }[] }>("/capture-guidance"), staleTime: Infinity });
  return (
    <details className="card" style={{ marginBottom: 14 }}>
      <summary><strong>Guided capture</strong> <span className="muted small">overlap, light, reference measurements, problem surfaces</span></summary>
      <div className="grid grid-2" style={{ marginTop: 10 }}>
        {g.data?.sections.map((s) => (
          <div key={s.heading}><h3>{s.heading}</h3><ul className="small">{s.points.map((p) => <li key={p}>{p}</li>)}</ul></div>
        ))}
      </div>
    </details>
  );
}

function DocButton({ asset }: { asset: Asset }) {
  const [text, setText] = useState<string | null>(null);
  const open = async () => {
    const u = await api.get<{ url: string }>(`/assets/${asset.id}/url`);
    setText(await (await fetch(sameOrigin(u.url))).text());
  };
  return (
    <>
      <button className="btn btn-small" onClick={open}>{asset.filename}</button>
      <Dialog open={text !== null} onClose={() => setText(null)} title={asset.filename} footer={<button className="btn" onClick={() => setText(null)}>Close</button>}>
        <pre className="json" style={{ whiteSpace: "pre-wrap" }}>{text}</pre>
        <p className="muted small mono">SHA-256 {asset.sha256}</p>
      </Dialog>
    </>
  );
}

function NewBatch() {
  const pid = useProjectId();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [kind, setKind] = useState("photos");
  const create = useMutation({
    mutationFn: () => api.post(`/projects/${pid}/capture-batches`, { name, kind }),
    onSuccess: () => { setName(""); qc.invalidateQueries({ queryKey: ["batches", pid] }); },
  });
  return (
    <Card title="New capture batch">
      <form className="row" style={{ alignItems: "end" }} onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <Field label="Name" htmlFor="b-name"><input id="b-name" required value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Kind" htmlFor="b-kind">
          <select id="b-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            {["photos", "video", "drone", "gaussian", "point_cloud", "mesh", "terrain", "model_files"].map((k) => <option key={k} value={k}>{k.replace("_", " ")}</option>)}
          </select>
        </Field>
        <button className="btn btn-primary" type="submit">Create batch</button>
      </form>
      <ErrorNote error={create.error} />
    </Card>
  );
}

function Batch({ batch, assets }: { batch: { id: string; name: string; kind: string; synthetic: boolean; created_at: string }; assets: Asset[] }) {
  const pid = useProjectId();
  const qc = useQueryClient();
  const [log, setLog] = useState<{ name: string; status: string }[]>([]);
  const [tol, setTol] = useState("0.10");
  const upload = async (files: FileList) => {
    for (const f of Array.from(files)) {
      setLog((l) => [...l, { name: f.name, status: "uploading" }]);
      const form = new FormData();
      form.append("file", f);
      form.append("capture_batch_id", batch.id);
      try {
        await api.upload(`/projects/${pid}/assets`, form);
        setLog((l) => l.map((x) => (x.name === f.name ? { ...x, status: "verified" } : x)));
      } catch (e) {
        setLog((l) => l.map((x) => (x.name === f.name ? { ...x, status: `rejected: ${(e as Error).message}` } : x)));
      }
    }
    qc.invalidateQueries({ queryKey: ["assets", pid] });
  };
  const start = useMutation({
    mutationFn: () => api.post(`/projects/${pid}/reconstructions`, { capture_batch_id: batch.id, settings: { tolerance_abs_m: Number(tol) } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recons", pid] }),
  });
  const media = assets.filter((a) => a.kind === "image" || a.kind === "video").length;
  return (
    <Card title={batch.name} intro={`${batch.kind.replace("_", " ")} · ${assets.length} files · created ${fmtDate(batch.created_at)}`}
      actions={batch.synthetic ? <span className="badge badge-synthetic">Synthetic</span> : undefined}>
      <div className="row">
        <label className="btn btn-small">
          Add files<input type="file" multiple className="visually-hidden" onChange={(e) => e.target.files && upload(e.target.files)} />
        </label>
        {media > 0 && (
          <>
            <label className="small" htmlFor={`tol-${batch.id}`}>Check tolerance (m)</label>
            <input id={`tol-${batch.id}`} style={{ width: 80 }} inputMode="decimal" value={tol} onChange={(e) => setTol(e.target.value)} />
            <button className="btn btn-small btn-primary" onClick={() => start.mutate()} disabled={start.isPending}>Reconstruct ({media} images)</button>
          </>
        )}
      </div>
      <ErrorNote error={start.error} />
      {log.length > 0 && <ul className="small">{log.map((l) => <li key={l.name}>{l.name}: {l.status}</li>)}</ul>}
      {assets.length > 0 && (
        <details style={{ marginTop: 8 }}>
          <summary>Files</summary>
          <div className="table-wrap" style={{ maxHeight: 280, overflowY: "auto" }}>
            <table className="data">
              <thead><tr><th>File</th><th>Kind</th><th className="num">Size</th><th>SHA-256</th><th>Status</th></tr></thead>
              <tbody>{assets.map((a) => <tr key={a.id}><td>{a.filename}</td><td>{a.kind}</td><td className="num">{fmtBytes(a.size_bytes)}</td><td className="mono">{a.sha256?.slice(0, 16)}…</td><td><StatusBadge status={a.status === "verified" ? "ok" : "failed"} label={a.status} /></td></tr>)}</tbody>
            </table>
          </div>
        </details>
      )}
    </Card>
  );
}

function ReconRow({ job }: { job: ReconJob }) {
  const pid = useProjectId();
  const qc = useQueryClient();
  const [live, setLive] = useState<ReconJob["stages"] | null>(null);
  useEffect(() => {
    if (["completed", "failed", "cancelled"].includes(job.status)) return;
    const ctl = new AbortController();
    streamEvents(`/reconstructions/${job.id}/events`, (e) => {
      const d = e.data as { status: string; stages: ReconJob["stages"] };
      setLive(d.stages);
      if (["completed", "failed", "cancelled"].includes(d.status)) qc.invalidateQueries({ queryKey: ["recons", pid] });
    }, ctl.signal).catch(() => undefined);
    return () => ctl.abort();
  }, [job.id, job.status, pid, qc]);
  const resume = useMutation({ mutationFn: () => api.post(`/reconstructions/${job.id}/resume`), onSuccess: () => qc.invalidateQueries({ queryKey: ["recons", pid] }) });
  const cancel = useMutation({ mutationFn: () => api.post(`/reconstructions/${job.id}/cancel`), onSuccess: () => qc.invalidateQueries({ queryKey: ["recons", pid] }) });
  const stages = live ?? job.stages;
  return (
    <div className="card" style={{ background: "var(--surface-2)" }}>
      <div className="row">
        <strong>Job {shortId(job.id)}</strong>
        <StatusBadge status={job.status} />
        <span className="muted small">{fmtDate(job.created_at)}</span>
        <div className="spacer" />
        {job.result_scene_revision_id && <Link className="btn btn-small" to={`../scene?rev=${job.result_scene_revision_id}`}>Open scene</Link>}
        {["failed", "cancelled", "completed"].includes(job.status) && <button className="btn btn-small" onClick={() => resume.mutate()}>Resume</button>}
        {job.status === "running" && <button className="btn btn-small" onClick={() => cancel.mutate()}>Cancel</button>}
      </div>
      <ol className="small" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
        {stages.map((s) => (
          <li key={s.name}>
            <StatusBadge status={s.status === "pending" ? "queued" : s.status === "skipped" ? "info" : s.status} label={s.status} /> {s.name.replace(/_/g, " ")}
            {s.message && <span className="muted"> — {s.message}</span>}
          </li>
        ))}
      </ol>
      {job.error && <ErrorNote error={job.error} />}
      {job.status === "completed" && stages.find((x) => x.name === "reconstruction_training")?.status === "skipped" && (
        <ExternalTraining job={job} />
      )}
    </div>
  );
}

interface SplatImport {
  scene_revision_id: string;
  gaussians: number;
  alignment_check: { median_distance_fraction_of_spread: number; spread_ratio_splat_to_sparse: number };
}

/** Train the splat on a free or rented GPU outside Parallel, then import it as a new scene revision. */
function ExternalTraining({ job }: { job: ReconJob }) {
  const pid = useProjectId();
  const qc = useQueryClient();
  const [picked, setPicked] = useState<File[]>([]);
  const pkg = useMutation({
    mutationFn: async () => {
      const a = await api.post<Asset>(`/reconstructions/${job.id}/training-package`);
      const u = await api.get<{ url: string }>(`/assets/${a.id}/url`);
      window.location.assign(sameOrigin(u.url));
    },
  });
  const notebook = useMutation({
    mutationFn: async () => {
      const text = await api.get<string>("/external-training/notebook");
      const url = URL.createObjectURL(new Blob([typeof text === "string" ? text : JSON.stringify(text)], { type: "application/x-ipynb+json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "parallel_splat_training.ipynb";
      a.click();
      URL.revokeObjectURL(url);
    },
  });
  const splat = picked.find((f) => f.name.toLowerCase().endsWith(".ply"));
  const info = picked.find((f) => f.name.toLowerCase().endsWith(".json"));
  const send = useMutation({
    mutationFn: () => {
      const f = new FormData();
      f.append("splat", splat as File);
      if (info) f.append("training_info", info);
      return api.upload<SplatImport>(`/reconstructions/${job.id}/import-splat`, f);
    },
    onSuccess: () => {
      setPicked([]);
      qc.invalidateQueries({ queryKey: ["recons", pid] });
      qc.invalidateQueries({ queryKey: ["scenes", pid] });
    },
  });
  const check = send.error instanceof ApiError ? (send.error.details as { median_distance_fraction_of_spread?: number; spread_ratio_splat_to_sparse?: number } | null) : null;
  return (
    <details style={{ marginTop: 10 }}>
      <summary><strong>Train the Gaussian splat on a free GPU</strong> <span className="muted small">(this reconstruction has only the untrained initialisation)</span></summary>
      <ol className="small" style={{ paddingLeft: 18 }}>
        <li>Download the training package: the frames and COLMAP model of this reconstruction, with a manifest of their hashes.</li>
        <li>Download the notebook, open it in Google Colab (or Kaggle) with a GPU runtime, and run it. It uploads nothing to Parallel.</li>
        <li>Import the <code>splat.ply</code> and <code>training_info.json</code> it produces. Parallel checks that the splat lines up with this reconstruction and adds it to the scene as a new revision, recorded as trained outside Parallel.</li>
      </ol>
      <div className="row">
        <button className="btn btn-small" onClick={() => pkg.mutate()} disabled={pkg.isPending}>{pkg.isPending ? "Preparing…" : "Download training package"}</button>
        <button className="btn btn-small" onClick={() => notebook.mutate()} disabled={notebook.isPending}>Download notebook</button>
      </div>
      <ErrorNote error={pkg.error ?? notebook.error} />
      <div className="row" style={{ marginTop: 8 }}>
        <label className="small" htmlFor={`imp-${job.id}`}>Trained splat (.ply) and training information (.json)</label>
        <input id={`imp-${job.id}`} type="file" multiple accept=".ply,.json" onChange={(e) => setPicked(Array.from(e.target.files ?? []))} />
        <button className="btn btn-small btn-primary" onClick={() => send.mutate()} disabled={!splat || send.isPending}>{send.isPending ? "Checking and importing…" : "Import trained splat"}</button>
      </div>
      <ErrorNote error={send.error} />
      {check?.median_distance_fraction_of_spread !== undefined && (
        <div className="muted small">Measured: median distance from the reconstruction's points to the nearest Gaussian was {(check.median_distance_fraction_of_spread * 100).toFixed(1)}% of the scene's spread (limit 5%); size ratio {check.spread_ratio_splat_to_sparse} (allowed 0.5 to 2).</div>
      )}
      {send.data && (
        <div className="note" role="status">
          Imported {send.data.gaussians.toLocaleString()} Gaussians; alignment check passed. <Link to={`../scene?rev=${send.data.scene_revision_id}`}>Open the new scene revision</Link>, then use <em>Load</em> beside the trained splat.
        </div>
      )}
    </details>
  );
}
