/** API client: bearer-token auth, consistent error objects, uploads and server-sent events over fetch. */

const TOKEN_KEY = "parallel.token";

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session lasts for this page only */
  }
  sessionToken = token;
}

let sessionToken: string | null = getToken();

function headers(extra?: HeadersInit): Headers {
  const h = new Headers(extra);
  const t = sessionToken ?? getToken();
  if (t) h.set("Authorization", `Bearer ${t}`);
  return h;
}

/** Authorization header for a direct fetch: none when signed out (open access serves the guest). */
export function authHeaders(): Record<string, string> {
  const t = sessionToken ?? getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function parse(res: Response): Promise<unknown> {
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") ?? "";
  if (type.includes("application/json")) return res.json();
  return res.text();
}

export async function request<T>(method: string, path: string, body?: unknown, init?: RequestInit): Promise<T> {
  const isForm = body instanceof FormData;
  const { headers: extra, ...rest } = init ?? {};
  const h = headers(extra);
  if (!isForm && body !== undefined) h.set("Content-Type", "application/json");
  const res = await fetch(`/api/v1${path}`, {
    ...rest,
    method,
    headers: h,
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  });
  const data = await parse(res);
  if (!res.ok) {
    const err = (data as { error?: { code: string; message: string; details?: unknown } })?.error;
    if (res.status === 401) window.dispatchEvent(new CustomEvent("parallel:unauthorised"));
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? `Request failed (${res.status})`, err?.details);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown, init?: RequestInit) => request<T>("POST", path, body ?? {}, init),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, form),
};

/** Signed storage URLs are absolute to the API's public origin; route them through this origin. */
export function sameOrigin(url: string): string {
  try {
    const u = new URL(url, window.location.origin);
    if (u.pathname.startsWith("/api/")) return `${u.pathname}${u.search}`;
    return url;
  } catch {
    return url;
  }
}

export interface StreamEvent {
  event: string;
  data: unknown;
}

/** Server-sent events with the Authorization header (EventSource cannot send headers). */
export async function streamEvents(path: string, onEvent: (e: StreamEvent) => void, signal: AbortSignal): Promise<void> {
  const res = await fetch(`/api/v1${path}`, { headers: headers({ Accept: "text/event-stream" }), signal });
  if (!res.ok || !res.body) throw new ApiError(res.status, "stream_failed", "Could not open the progress stream");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) {
        try {
          onEvent({ event, data: JSON.parse(data.join("\n")) });
        } catch {
          onEvent({ event, data: data.join("\n") });
        }
      }
    }
  }
}
