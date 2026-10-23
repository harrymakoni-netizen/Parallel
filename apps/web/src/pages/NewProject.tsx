import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useTemplates } from "../api/hooks";
import type { Project } from "../api/types";
import { useOrg } from "../components/Shell";
import { Card, ErrorNote, Field, PageHead } from "../components/ui";
import { PROJECT_TYPES } from "./common";

export function NewProject() {
  const org = useOrg();
  const nav = useNavigate();
  const qc = useQueryClient();
  const templates = useTemplates();
  const [f, setF] = useState({ name: "", project_type: "mine_evacuation", description: "", location_label: "", lon: "", lat: "", crs: "", ox: "", oy: "", template: "" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });
  const create = useMutation({
    mutationFn: () =>
      api.post<Project>("/projects", {
        organisation_id: org.id,
        name: f.name,
        project_type: f.project_type,
        description: f.description,
        location_label: f.location_label || null,
        location_lonlat: f.lon && f.lat ? [Number(f.lon), Number(f.lat)] : null,
        georeference: f.crs && f.ox && f.oy
          ? { horizontal_crs: f.crs, local_origin_projected: [Number(f.ox), Number(f.oy)], vertical_reference: "unknown" }
          : null,
        template: f.template || null,
      }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      nav(`/p/${p.id}`);
    },
  });
  return (
    <div className="page" style={{ maxWidth: 820 }}>
      <PageHead title="New project" intro="Start from captures, existing model files or a prepared example. Membership can be added after creation." />
      <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <Card>
          <div className="stack">
            <Field label="Name" htmlFor="np-name"><input id="np-name" required minLength={2} value={f.name} onChange={set("name")} /></Field>
            <Field label="Project type" htmlFor="np-type">
              <select id="np-type" value={f.project_type} onChange={set("project_type")}>
                {Object.entries(PROJECT_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
            <Field label="Description" htmlFor="np-desc"><textarea id="np-desc" value={f.description} onChange={set("description")} /></Field>
            <div className="grid grid-3">
              <Field label="Location label" htmlFor="np-loc"><input id="np-loc" value={f.location_label} onChange={set("location_label")} /></Field>
              <Field label="Longitude" htmlFor="np-lon" hint="WGS84 degrees, optional"><input id="np-lon" inputMode="decimal" value={f.lon} onChange={set("lon")} /></Field>
              <Field label="Latitude" htmlFor="np-lat" hint="WGS84 degrees, optional"><input id="np-lat" inputMode="decimal" value={f.lat} onChange={set("lat")} /></Field>
            </div>
            <div className="grid grid-3">
              <Field label="Projected CRS" htmlFor="np-crs" hint="If known, e.g. EPSG:32735. Geographic CRSs are refused for metric work.">
                <input id="np-crs" value={f.crs} onChange={set("crs")} />
              </Field>
              <Field label="Local origin easting (m)" htmlFor="np-ox"><input id="np-ox" inputMode="decimal" value={f.ox} onChange={set("ox")} /></Field>
              <Field label="Local origin northing (m)" htmlFor="np-oy"><input id="np-oy" inputMode="decimal" value={f.oy} onChange={set("oy")} /></Field>
            </div>
            <Field label="Start from" htmlFor="np-tpl" hint="Templates add clearly labelled synthetic starter models and scenarios. They never add results.">
              <select id="np-tpl" value={f.template} onChange={set("template")}>
                <option value="">Empty project (captures or model files)</option>
                {templates.data?.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
              </select>
            </Field>
            <ErrorNote error={create.error} />
            <div className="row"><button className="btn btn-primary" type="submit" disabled={create.isPending}>{create.isPending ? "Creating…" : "Create project"}</button></div>
          </div>
        </Card>
      </form>
    </div>
  );
}
