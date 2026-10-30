import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, setToken } from "../api/client";
import { Logo } from "../components/Shell";
import { ErrorNote, Field } from "../components/ui";

export function Login({ open = false }: { open?: boolean }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const loc = useLocation();
  const qc = useQueryClient();
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ access_token: string }>("/auth/login", { email, password });
      setToken(r.access_token);
      qc.removeQueries({ queryKey: ["me"] });
      nav((loc.state as { from?: string } | null)?.from ?? "/projects", { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login">
      <form className="card login-card stack" onSubmit={submit} aria-labelledby="login-title">
        <Logo />
        <h1 id="login-title">Sign in to Parallel</h1>
        <p className="muted">Capture real places, simulate them with established engines, and compare scenarios.</p>
        <Field label="Email" htmlFor="email">
          <input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password" htmlFor="password">
          <input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorNote error={error} />
        <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        {open && (
          <button className="btn" type="button" onClick={() => { setToken(null); qc.clear(); nav("/projects"); }}>
            Continue without signing in
          </button>
        )}
      </form>
    </div>
  );
}
