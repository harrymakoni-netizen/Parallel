import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { getToken, setToken } from "./api/client";
import { useAuthConfig, useMe } from "./api/hooks";
import type { AuthConfig } from "./api/types";
import { Shell } from "./components/Shell";
import { Loading } from "./components/ui";
import { AllReports } from "./pages/AllReports";
import { Demonstrations } from "./pages/Demonstrations";
import { EngineLibrary } from "./pages/EngineLibrary";
import { Login } from "./pages/Login";
import { NewProject } from "./pages/NewProject";
import { OrgSettings } from "./pages/OrgSettings";
import { Projects } from "./pages/Projects";
import { ProjectLayout } from "./pages/project/ProjectLayout";

export default function App() {
  const nav = useNavigate();
  const loc = useLocation();
  const qc = useQueryClient();
  const onLogin = loc.pathname === "/login";
  const config = useAuthConfig();
  const open = !!config.data?.open_access; // open access: no sign-in needed, the server serves a shared guest
  const me = useMe(!onLogin && config.isFetched && (!!getToken() || open));
  useEffect(() => {
    const onUnauth = () => {
      if (window.location.pathname === "/login") return;
      setToken(null);
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth-config" });
      // read the setting now: this handler can be registered before the configuration has loaded
      const isOpen = !!qc.getQueryData<AuthConfig>(["auth-config"])?.open_access;
      nav(isOpen ? "/projects" : "/login", { replace: true, state: { from: loc.pathname } });
    };
    window.addEventListener("parallel:unauthorised", onUnauth);
    return () => window.removeEventListener("parallel:unauthorised", onUnauth);
  }, [nav, loc.pathname, qc]);

  if (config.isLoading) return <div className="page"><Loading label="Starting" /></div>;
  if (onLogin) return <Login open={open} />;
  if (!getToken() && !open) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  if (me.isLoading) return <div className="page"><Loading label={open && !getToken() ? "Opening Parallel" : "Signing in"} /></div>;
  if (me.isError || !me.data) {
    if (open) return <div className="page"><p role="alert">Parallel could not be reached. Reload the page, or <a href="/login">sign in</a>.</p></div>;
    return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  }
  return (
    <Shell me={me.data}>
      <Routes>
        <Route path="/" element={<Navigate to="/projects" replace />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/projects/new" element={<NewProject />} />
        <Route path="/demonstrations" element={<Demonstrations />} />
        <Route path="/engines" element={<EngineLibrary />} />
        <Route path="/reports" element={<AllReports />} />
        <Route path="/settings" element={<OrgSettings />} />
        <Route path="/p/:pid/*" element={<ProjectLayout />} />
        <Route path="*" element={<div className="page"><h1>Not found</h1></div>} />
      </Routes>
    </Shell>
  );
}
