import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { setToken } from "../api/client";
import type { Me } from "../api/types";

const MeContext = createContext<Me | null>(null);
export const useMeContext = () => useContext(MeContext)!;
export const useOrg = () => useMeContext().organisations[0];

const THEME_KEY = "parallel.theme";
function readTheme(): string {
  try {
    return localStorage.getItem(THEME_KEY) ?? "system";
  } catch {
    return "system";
  }
}
function applyTheme(t: string) {
  if (t === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  try {
    localStorage.setItem(THEME_KEY, t);
  } catch {
    /* per-viewer convenience only */
  }
}
applyTheme(readTheme());

export function Logo() {
  return (
    <span className="logo-tile">
      <img src="/brand/parallel-logo.webp" alt="Parallel" width={72} height={30} />
    </span>
  );
}

export function Shell({ me, children }: { me: Me; children: ReactNode }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [theme, setTheme] = useState(readTheme());
  const signOut = () => {
    setToken(null);
    qc.clear();
    nav("/login");
  };
  const cycle = () => {
    const next = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
    applyTheme(next);
    setTheme(next);
  };
  return (
    <MeContext.Provider value={me}>
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="topbar">
        <NavLink to="/projects" aria-label="Parallel projects"><Logo /></NavLink>
        <nav className="topnav" aria-label="Main">
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/demonstrations">Demonstrations</NavLink>
          <NavLink to="/engines">Engine Library</NavLink>
          <NavLink to="/reports">Reports</NavLink>
          <NavLink to="/settings">Organisation Settings</NavLink>
        </nav>
        <div className="topbar-right">
          <button className="btn btn-ghost btn-small" onClick={cycle} aria-label={`Theme: ${theme}. Change theme`}>
            Theme: {theme}
          </button>
          <span className="small" style={{ color: "#c5d2e6" }}>{me.user.display_name}</span>
          <button className="btn btn-ghost btn-small" onClick={signOut}>Sign out</button>
        </div>
      </header>
      <main id="main" tabIndex={-1}>{children}</main>
    </MeContext.Provider>
  );
}
