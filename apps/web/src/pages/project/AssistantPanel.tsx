import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { AssistantItem } from "../../api/types";
import { ErrorNote, Icon, StatusBadge } from "../../components/ui";
import { useProjectId } from "./ProjectLayout";

interface Session { id: string; provider: string; model: string }

export default function AssistantPanel({ onClose }: { onClose: () => void }) {
  const pid = useProjectId();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["assistant-status", pid], queryFn: () => api.get<{ available: boolean; reason: string | null; provider: string | null; model: string | null }>(`/projects/${pid}/assistant/status`) });
  const sessions = useQuery({ queryKey: ["assistant-sessions", pid], enabled: !!status.data?.available, queryFn: () => api.get<Session[]>(`/projects/${pid}/assistant/sessions`) });
  const [sid, setSid] = useState<string | null>(null);
  const session = sid ?? sessions.data?.[0]?.id ?? null;
  const items = useQuery({ queryKey: ["assistant-items", session], enabled: !!session, queryFn: () => api.get<{ items: AssistantItem[] }>(`/assistant/sessions/${session}`) });
  const [text, setText] = useState("");
  const log = useRef<HTMLDivElement>(null);
  const start = useMutation({ mutationFn: () => api.post<Session>(`/projects/${pid}/assistant/sessions`), onSuccess: (s) => { setSid(s.id); qc.invalidateQueries({ queryKey: ["assistant-sessions", pid] }); } });
  const send = useMutation({
    mutationFn: async () => {
      let s = session;
      if (!s) { s = (await api.post<Session>(`/projects/${pid}/assistant/sessions`)).id; setSid(s); }
      return api.post(`/assistant/sessions/${s}/messages`, { text });
    },
    onSuccess: () => { setText(""); qc.invalidateQueries({ queryKey: ["assistant-items"] }); qc.invalidateQueries({ queryKey: ["scenarios", pid] }); qc.invalidateQueries({ queryKey: ["runs", pid] }); },
  });
  const adopt = useMutation({ mutationFn: (id: string) => api.post(`/assistant/tool-calls/${id}/adopt`), onSuccess: () => qc.invalidateQueries() });
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [items.data, send.isPending]);
  return (
    <aside className="assistant" id="assistant-panel" aria-label="Assistant">
      <div className="assistant-head">
        <Icon name="spark" />
        <strong>Assistant</strong>
        {status.data?.model && <span className="muted small">{status.data.provider} · {status.data.model}</span>}
        <div className="spacer" />
        {status.data?.available && <button className="btn btn-small btn-ghost" onClick={() => start.mutate()}>New chat</button>}
        <button className="btn btn-small btn-ghost" onClick={onClose} aria-label="Close assistant"><Icon name="x" /></button>
      </div>
      {status.data && !status.data.available ? (
        <div className="assistant-log">
          <div className="note warn"><strong>The assistant is unavailable.</strong> {status.data.reason}</div>
          <p className="muted small">Every modelling and simulation workflow works without it.</p>
        </div>
      ) : (
        <>
          <div className="assistant-log" ref={log} aria-live="polite">
            {!items.data?.items.length && (
              <div className="muted small">
                Ask about this project, for example “Compare evacuation with the eastern route unavailable” or “Run the same rainfall with additional storage”.
                The assistant works through scoped tools with your permissions; drafts must be adopted by you before they run.
              </div>
            )}
            {items.data?.items.map((it) => <Item key={`${it.type}-${it.seq}`} it={it} onAdopt={(id) => adopt.mutate(id)} pid={pid} />)}
            {send.isPending && <div className="row muted small"><span className="spinner" aria-hidden="true" /> Working…</div>}
            <ErrorNote error={send.error ?? adopt.error} />
          </div>
          <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) send.mutate(); }}>
            <label className="visually-hidden" htmlFor="as-text">Message</label>
            <textarea id="as-text" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about this project"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (text.trim()) send.mutate(); } }} />
            <button className="btn btn-primary" disabled={send.isPending || !text.trim()}>Send</button>
          </form>
        </>
      )}
    </aside>
  );
}

function Item({ it, onAdopt, pid }: { it: AssistantItem; onAdopt: (id: string) => void; pid: string }) {
  if (it.type === "user") return <div className="bubble user">{it.text}</div>;
  if (it.type === "assistant") return <div className="bubble assistant">{it.text}{it.fallback && <div className="muted small">Answered by {it.served_by} after a fallback.</div>}</div>;
  if (it.type === "notice") return <div className="note warn small">{it.text}</div>;
  const res = it.result as Record<string, unknown> | undefined;
  return (
    <div className="toolcall">
      <div className="row" style={{ gap: 6 }}>
        <span className="mono">{it.tool}</span>
        {it.error ? <StatusBadge status={it.authorised ? "failed" : "warning"} label={it.authorised ? "Failed" : "Not permitted"} /> : <StatusBadge status="ok" label="Done" />}
      </div>
      {it.error && <div className="muted">{it.error}</div>}
      {it.suggestion && !it.error && (
        <div className="row" style={{ marginTop: 6 }}>
          {it.adopted_by ? <StatusBadge status="ok" label="Adopted" /> : <button className="btn btn-small btn-primary" onClick={() => onAdopt(it.id!)}>Review and adopt</button>}
          {it.tool === "create_scenario_draft" && <Link to={`/p/${pid}/scenarios`}>Open scenarios</Link>}
          {typeof res?.next_step === "string" && <span className="muted">{res.next_step}</span>}
        </div>
      )}
      {it.tool === "submit_run" && typeof res?.run_id === "string" && <Link to={`/p/${pid}/runs/${res.run_id}`}>Open run</Link>}
    </div>
  );
}
