import { useEffect, useId, useRef, type ReactNode } from "react";
import { ApiError } from "../api/client";

/* ---------------------------------------------------------------- formatting */
export function fmtNum(v: number | null | undefined, sig = 4): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (v !== 0 && (Math.abs(v) >= 1e9 || Math.abs(v) < 1e-3)) return v.toExponential(2);
  return Number(v.toPrecision(sig)).toLocaleString(undefined, { maximumFractionDigits: 6 });
}
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
export function fmtBytes(n: number): string {
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}
export const shortId = (id: string | null | undefined) => (id ? id.slice(0, 8) : "—");
export const humanise = (s: string) => s.replace(/[._]/g, " ").replace(/^\w/, (c) => c.toUpperCase());

/* ---------------------------------------------------------------- icons (decorative; text always accompanies) */
type IconName = "check" | "warning" | "error" | "info" | "clock" | "play" | "pause" | "x" | "dot" | "lock" | "spark";
const PATHS: Record<IconName, string> = {
  check: "M3.5 8.5l3 3 6-7",
  warning: "M8 2.5l6 11H2l6-11zM8 6.5v3.2M8 11.6v.4",
  error: "M8 1.8a6.2 6.2 0 100 12.4A6.2 6.2 0 008 1.8zM5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8",
  info: "M8 1.8a6.2 6.2 0 100 12.4A6.2 6.2 0 008 1.8zM8 7.2v4.2M8 4.8v.4",
  clock: "M8 1.8a6.2 6.2 0 100 12.4A6.2 6.2 0 008 1.8zM8 4.6V8l2.4 1.6",
  play: "M5 3.5l7 4.5-7 4.5z",
  pause: "M5 3.5v9M11 3.5v9",
  x: "M4 4l8 8M12 4l-8 8",
  dot: "M8 6.5a1.5 1.5 0 100 3 1.5 1.5 0 000-3z",
  lock: "M4.5 7.5h7v6h-7zM6 7.5V5.5a2 2 0 014 0v2",
  spark: "M8 1.5l1.4 4.1 4.1 1.4-4.1 1.4L8 12.5 6.6 8.4 2.5 7l4.1-1.4z",
};
export function Icon({ name, size = 14 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor"
      strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d={PATHS[name]} />
    </svg>
  );
}

/* ---------------------------------------------------------------- status */
type Tone = "good" | "warning" | "critical" | "accent" | "neutral";
const STATE_TONE: Record<string, [Tone, IconName]> = {
  completed: ["good", "check"],
  ready: ["good", "check"],
  ok: ["good", "check"],
  approved: ["good", "check"],
  reviewed: ["good", "check"],
  available: ["good", "check"],
  satisfied: ["good", "check"],
  running: ["accent", "clock"],
  queued: ["accent", "clock"],
  validating: ["accent", "clock"],
  preparing: ["accent", "clock"],
  collecting: ["accent", "clock"],
  postprocessing: ["accent", "clock"],
  generating: ["accent", "clock"],
  awaiting_external_result: ["warning", "clock"],
  warning: ["warning", "warning"],
  unattained: ["warning", "warning"],
  licence_required: ["warning", "lock"],
  configuration_required: ["warning", "warning"],
  worker_unavailable: ["warning", "warning"],
  integration_incomplete: ["warning", "warning"],
  disputed: ["warning", "warning"],
  info: ["neutral", "info"],
  failed: ["critical", "error"],
  error: ["critical", "error"],
  violated: ["critical", "error"],
  cancelled: ["neutral", "x"],
  unavailable: ["neutral", "info"],
  not_installed: ["neutral", "info"],
};
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const [tone, icon] = STATE_TONE[status] ?? ["neutral", "dot"];
  return (
    <span className={`badge ${tone !== "neutral" ? `badge-${tone}` : ""}`}>
      <Icon name={icon} size={12} />
      {label ?? humanise(status)}
    </span>
  );
}
export function ProvenanceBadge({ kind }: { kind: string }) {
  if (kind === "synthetic") return <span className="badge badge-synthetic">Synthetic</span>;
  if (kind === "ai_suggested") return <span className="badge badge-warning"><Icon name="spark" size={12} />AI suggested</span>;
  return <span className="badge">{humanise(kind)}</span>;
}

/* ---------------------------------------------------------------- layout */
export function PageHead({ title, intro, children }: { title: string; intro?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {intro && <p>{intro}</p>}
      </div>
      {children && <div className="row">{children}</div>}
    </div>
  );
}
export function Card({ title, intro, actions, children, id }: {
  title?: ReactNode; intro?: ReactNode; actions?: ReactNode; children: ReactNode; id?: string;
}) {
  return (
    <section className="card" aria-labelledby={id}>
      {(title || actions) && (
        <div className="card-head">
          <div>
            {title && <h2 id={id}>{title}</h2>}
            {intro && <p>{intro}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="row muted" role="status">
      <span className="spinner" aria-hidden="true" /> {label}…
    </div>
  );
}
export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error);
  const details = error instanceof ApiError ? error.details : null;
  const problems = (details as { problems?: string[] } | null)?.problems;
  return (
    <div className="note error" role="alert">
      <strong>{msg}</strong>
      {problems && (
        <ul>
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
export function Field({ label, hint, children, htmlFor }: { label: string; hint?: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}
export function KV({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ---------------------------------------------------------------- tabs (WAI-ARIA tablist) */
export function Tabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: { id: T; label: string }[]; value: T; onChange: (v: T) => void; label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const onKey = (e: React.KeyboardEvent) => {
    const i = tabs.findIndex((t) => t.id === value);
    const next = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : -1;
    if (next >= 0) {
      e.preventDefault();
      onChange(tabs[next].id);
      ref.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
    }
  };
  return (
    <div className="tabs" role="tablist" aria-label={label} ref={ref} onKeyDown={onKey}>
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={t.id === value} tabIndex={t.id === value ? 0 : -1} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- dialog (native <dialog>, focus handled by the browser) */
export function Dialog({ open, onClose, title, children, footer }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} aria-labelledby={id} onClose={onClose} onCancel={onClose}>
      <div className="dialog-body stack">
        <h2 id={id}>{title}</h2>
        {children}
      </div>
      {footer && <div className="dialog-foot">{footer}</div>}
    </dialog>
  );
}

export function JsonView({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}
