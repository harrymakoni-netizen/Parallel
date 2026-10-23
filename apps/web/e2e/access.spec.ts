import { expect, login, test } from "./support";

/**
 * Access control as a person experiences it: an organisation member who is not on a project cannot open
 * it, and a project viewer cannot see case records they are not assigned to or change anything.
 * The test creates its own member and removes the memberships afterwards.
 */
test("organisation, project and case boundaries hold in the browser and the API", async ({ browser, request, token, demos }) => {
  const admin = { Authorization: `Bearer ${token}` };
  const orgs = await (await request.get("/api/v1/organisations", { headers: admin })).json();
  const org = orgs.find((o: { slug: string }) => o.slug === "parallel-demonstrations");
  expect(org, "the demonstration organisation").toBeTruthy();
  const email = `e2e-member-${Date.now()}@parallel-demo.example`;
  const password = `e2e-${Math.random().toString(36).slice(2)}-pass`;
  const member = await request.post(`/api/v1/organisations/${org.id}/members`, {
    headers: admin, data: { email, password, role: "member", display_name: "E2E member" },
  });
  expect(member.status()).toBe(201);
  const userId = (await member.json()).user.id;
  const crash = demos.crashlens.id;
  try {
    const memberToken = await login(request, email, password);
    const as = { Authorization: `Bearer ${memberToken}` };

    // not on the project: it is invisible, and opening it directly shows no data
    const list = await (await request.get("/api/v1/projects?limit=100", { headers: as })).json();
    expect(list.items.map((p: { id: string }) => p.id)).not.toContain(crash);
    expect((await request.get(`/api/v1/projects/${crash}/overview`, { headers: as })).status()).toBe(404);
    const ctx = await browser.newContext();
    await ctx.addInitScript((t) => localStorage.setItem("parallel.token", t), memberToken);
    const page = await ctx.newPage();
    await page.goto(`/p/${crash}`);
    await expect(page.getByRole("alert")).toContainText("not found");

    // a project viewer: may read the project, but not unassigned case records, and may not change things
    expect((await request.post(`/api/v1/projects/${crash}/members`, { headers: admin, data: { user_id: userId, role: "viewer" } })).status()).toBe(201);
    await page.goto(`/p/${crash}/evidence`);
    await expect(page.getByText("Case records are visible only to assigned investigators and reviewers.")).toBeVisible();
    const cases = await (await request.get(`/api/v1/projects/${crash}/cases`, { headers: as })).json();
    expect(cases).toEqual([]);
    const runs = await (await request.get(`/api/v1/projects/${crash}/runs`, { headers: as })).json();
    expect(runs.items, "case-scoped runs are hidden from people outside the case").toEqual([]);
    const denied = await request.post(`/api/v1/projects/${crash}/scenes`, { headers: as, data: { name: "Not allowed" } });
    expect(denied.status()).toBe(403);
    await ctx.close();
  } finally {
    await request.delete(`/api/v1/organisations/${org.id}/members/${userId}`, { headers: admin });
  }
  // once removed from the organisation, the member keeps no access
  const after = await request.post("/api/v1/auth/login", { data: { email, password } });
  if (after.ok()) {
    const t = (await after.json()).access_token;
    expect((await request.get(`/api/v1/projects/${crash}/overview`, { headers: { Authorization: `Bearer ${t}` } })).status()).toBe(404);
  }
});
