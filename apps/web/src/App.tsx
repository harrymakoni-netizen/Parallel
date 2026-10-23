import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { getToken, setToken } from "./api/client";
import { useMe } from "./api/hooks";
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
  const me = useMe(!onLogin && !!getToken());
  useEffect(() => {
    const onUnauth = () => {
      if (window.location.pathname === "/login") return;
      setToken(null);
      qc.removeQueries();
      nav("/login", { replace: true, state: { from: loc.pathname } });
    };
    window.addEventListener("parallel:unauthorised", onUnauth);
    return () => window.removeEventListener("parallel:unauthorised", onUnauth);
  }, [nav, loc.pathname, qc]);

  if (onLogin) return <Login />;
  if (!getToken()) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  if (me.isLoading) return <div className="page"><Loading label="Signing in" /></div>;
  if (me.isError || !me.data) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
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
