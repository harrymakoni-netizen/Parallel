import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { useEngines } from "../api/hooks";
import { useOrg } from "../components/Shell";
import { ErrorNote, Loading, PageHead, StatusBadge } from "../components/ui";
import { DISCIPLINES } from "./common";

export function EngineLibrary() {
  const org = useOrg();
  const q = useEngines(org?.id);
  const qc = useQueryClient();
  const [disc, setDisc] = useState("");
  const check = useMutation({
    mutationFn: (engine: string) => api.post(`/engines/${engine}/check?organisation_id=${org.id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["engines"] }),
  });
  const engines = (q.data ?? []).filter((e) => !disc || e.capability.disciplines.includes(disc));
  return (
    <div className="page">
      <PageHead title="Engine Library" intro="All fifteen engines in scope, with their real availability on this installation. Licensed engines become ready only when your organisation registers a licensed installation; nothing is simulated in their place.">
        <label className="visually-hidden" htmlFor="eng-disc">Filter by discipline</label>
        <select id="eng-disc" value={disc} onChange={(e) => setDisc(e.target.value)}>
          <option value="">All disciplines</option>
          {Object.entries(DISCIPLINES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </PageHead>
      {q.isLoading && <Loading />}
      <ErrorNote error={q.error ?? check.error} />
      <div className="grid grid-2">
        {engines.map((e) => {
          const c = e.capability;
          return (
            <article key={c.engine_id} className="card" aria-labelledby={`eng-${c.engine_id}`}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                <div>
                  <h2 id={`eng-${c.engine_id}`}>{c.name}</h2>
                  <p className="muted small">{c.vendor} · adapter {c.adapter_version}</p>
                </div>
                <button className="btn btn-small" onClick={() => check.mutate(c.engine_id)} disabled={check.isPending}>Check now</button>
              </div>
              <p style={{ marginTop: 8 }}>{c.role}</p>
              <div className="row" style={{ marginTop: 8 }}>
                {c.disciplines.map((d) => <span key={d} className="badge">{DISCIPLINES[d] ?? d}</span>)}
              </div>
              <table className="data" style={{ marginTop: 10 }}>
                <thead><tr><th>Mode</th><th>Availability</th><th>Detail</th></tr></thead>
                <tbody>
                  {e.availability.map((a) => (
                    <tr key={a.execution_mode}>
                      <td>{a.mode_label}{a.execution_mode === e.default_mode ? " (default)" : ""}</td>
                      <td><StatusBadge status={a.availability} label={a.availability_label} /></td>
                      <td className="small">
                        {a.detected_version && <div>Detected {a.detected_version}</div>}
                        {a.details.slice(0, 2).map((d) => <div key={d} className="muted">{d}</div>)}
                        {a.remedy && <div><strong>Next step:</strong> {a.remedy}</div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <details style={{ marginTop: 8 }}>
                <summary>Limitations, licence and tested versions</summary>
                <ul className="small">{c.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
                <p className="small"><strong>Licence:</strong> {c.licence.name} ({c.licence.kind.replace("_", " ")}){c.licence.note ? `. ${c.licence.note}` : ""}</p>
                <p className="small"><strong>Versions:</strong> {c.tested_versions.map((t) => `${t.version} (${t.status.replace("_", " ")})`).join(", ") || "—"}</p>
                {c.official_docs.length > 0 && <p className="small"><a href={c.official_docs[0]} target="_blank" rel="noreferrer">Official documentation</a></p>}
              </details>
            </article>
          );
        })}
      </div>
    </div>
  );
}
