import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Member, Organisation } from "../api/types";
import { useOrg } from "../components/Shell";
import { Card, ErrorNote, Field, PageHead, StatusBadge, fmtDate } from "../components/ui";

const ENGINE_IDS = ["ventsim", "flac3d", "3dec", "virtualcrash", "pccrash", "hecras", "fds", "energyplus", "isaacsim"];

export function OrgSettings() {
  const org = useOrg();
  const qc = useQueryClient();
  const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => api.get<Organisation[]>("/organisations") });
  const current = orgs.data?.find((o) => o.id === org.id);
  const members = useQuery({ queryKey: ["org-members", org.id], queryFn: () => api.get<Member[]>(`/organisations/${org.id}/members`) });
  const installs = useQuery({ queryKey: ["installs", org.id], queryFn: () => api.get<Record<string, unknown>[]>(`/organisations/${org.id}/engine-installations`) });
  const isAdmin = org.role === "admin";
  const settings = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patch(`/organisations/${org.id}/settings`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["orgs"] }),
  });
  const [m, setM] = useState({ email: "", display_name: "", role: "member", password: "" });
  const addMember = useMutation({
    mutationFn: () => api.post(`/organisations/${org.id}/members`, { ...m, password: m.password || null }),
    onSuccess: () => { setM({ email: "", display_name: "", role: "member", password: "" }); qc.invalidateQueries({ queryKey: ["org-members"] }); },
  });
  const [inst, setInst] = useState({ engine_id: "ventsim", label: "", edition: "", version: "", licence_confirmed: false, operator_contact: "" });
  const register = useMutation({
    mutationFn: () => api.post(`/organisations/${org.id}/engine-installations`, inst),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["installs"] }); qc.invalidateQueries({ queryKey: ["engines"] }); },
  });
  const s = (current?.settings ?? {}) as Record<string, unknown>;
  return (
    <div className="page" style={{ maxWidth: 1000 }}>
      <PageHead title="Organisation Settings" intro={`${org.name} · your role: ${org.role}`} />
      <Card title="AI data policy" intro="The assistant is optional. Every modelling and simulation workflow works without it.">
        <div className="stack">
          <label className="row">
            <input type="checkbox" disabled={!isAdmin} checked={!!s.allow_external_ai}
              onChange={(e) => settings.mutate({ allow_external_ai: e.target.checked })} />
            Allow project data to be sent to the configured language-model provider
          </label>
          <label className="row">
            <input type="checkbox" disabled={!isAdmin} checked={!!s.allow_case_media_to_ai}
              onChange={(e) => settings.mutate({ allow_case_media_to_ai: e.target.checked })} />
            Allow CrashLens case records to be sent to the provider (media files are never sent)
          </label>
          <ErrorNote error={settings.error} />
        </div>
      </Card>
      <Card title="Members">
        <table className="data">
          <thead><tr><th>Name</th><th>Email</th><th>Role</th></tr></thead>
          <tbody>{members.data?.map((x) => <tr key={x.user.id}><td>{x.user.display_name}</td><td>{x.user.email}</td><td>{x.role}</td></tr>)}</tbody>
        </table>
        {isAdmin && (
          <form className="grid grid-4" style={{ marginTop: 12, alignItems: "end" }} onSubmit={(e) => { e.preventDefault(); addMember.mutate(); }}>
            <Field label="Email" htmlFor="m-email"><input id="m-email" type="email" required value={m.email} onChange={(e) => setM({ ...m, email: e.target.value })} /></Field>
            <Field label="Display name" htmlFor="m-name"><input id="m-name" value={m.display_name} onChange={(e) => setM({ ...m, display_name: e.target.value })} /></Field>
            <Field label="Role" htmlFor="m-role">
              <select id="m-role" value={m.role} onChange={(e) => setM({ ...m, role: e.target.value })}><option value="member">Member</option><option value="admin">Administrator</option></select>
            </Field>
            <Field label="Initial password" htmlFor="m-pw" hint="Local identity only"><input id="m-pw" type="password" value={m.password} onChange={(e) => setM({ ...m, password: e.target.value })} /></Field>
            <div><button className="btn btn-primary" type="submit">Add member</button></div>
            <ErrorNote error={addMember.error} />
          </form>
        )}
      </Card>
      <Card title="Licensed engine installations" intro="Register an installation your organisation is licensed to use. File-exchange runs then produce packages for that installation; results are imported with operator provenance.">
        <table className="data">
          <thead><tr><th>Engine</th><th>Installation</th><th>Licence</th><th>Last check</th></tr></thead>
          <tbody>
            {installs.data?.map((i) => (
              <tr key={String(i.id)}>
                <td>{String(i.engine_id)}</td>
                <td>{String(i.label)}<div className="muted small">{String(i.edition ?? "")}</div></td>
                <td>{i.licence_confirmed ? <StatusBadge status="ready" label="Confirmed" /> : <StatusBadge status="licence_required" label="Not confirmed" />}</td>
                <td className="small">{String((i.last_check as { availability?: string })?.availability ?? "—")} · {fmtDate(i.last_checked_at as string)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {isAdmin && (
          <form className="grid grid-3" style={{ marginTop: 12, alignItems: "end" }} onSubmit={(e) => { e.preventDefault(); register.mutate(); }}>
            <Field label="Engine" htmlFor="i-eng">
              <select id="i-eng" value={inst.engine_id} onChange={(e) => setInst({ ...inst, engine_id: e.target.value })}>{ENGINE_IDS.map((x) => <option key={x}>{x}</option>)}</select>
            </Field>
            <Field label="Installation label" htmlFor="i-label"><input id="i-label" required value={inst.label} onChange={(e) => setInst({ ...inst, label: e.target.value })} /></Field>
            <Field label="Edition and version" htmlFor="i-ed"><input id="i-ed" value={inst.edition} onChange={(e) => setInst({ ...inst, edition: e.target.value })} /></Field>
            <Field label="Operator contact" htmlFor="i-op"><input id="i-op" value={inst.operator_contact} onChange={(e) => setInst({ ...inst, operator_contact: e.target.value })} /></Field>
            <label className="row"><input type="checkbox" checked={inst.licence_confirmed} onChange={(e) => setInst({ ...inst, licence_confirmed: e.target.checked })} /> I confirm this installation is licensed for our use</label>
            <div><button className="btn btn-primary" type="submit">Register installation</button></div>
            <ErrorNote error={register.error} />
          </form>
        )}
      </Card>
    </div>
  );
}
