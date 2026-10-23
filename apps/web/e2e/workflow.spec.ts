import { expect, open, test } from "./support";

interface Run { id: string; state: string; engine_id: string; label: string | null; scenario_id: string }
interface Comparison { id: string; name: string }

test.describe("demonstration workflow", () => {
  test("the demonstrations page lists five synthetic projects with their comparisons", async ({ page, errors }) => {
    await open(page, "/demonstrations");
    const cards = page.locator(".card", { has: page.getByText("Comparison:") });
    await expect(cards).toHaveCount(5);
    for (const name of ["Mine Evacuation", "CrashLens", "Construction Sites", "Urban Planning", "Urban Drainage"]) {
      await expect(page.getByText(name, { exact: true }).first()).toBeVisible();
    }
    await expect(page.locator(".badge", { hasText: "Synthetic" })).toHaveCount(5);
    void errors;
  });

  test("the scene workspace draws prepared geometry and plays back an engine trajectory", async ({ page, demos, api, errors }) => {
    const pid = demos.mine_evacuation.id;
    const runs = await api<{ items: Run[] }>(`/projects/${pid}/runs`);
    const run = runs.items.find((r) => r.state === "completed" && r.engine_id === "jupedsim")!;
    await page.goto(`/p/${pid}/scene?run=${run.id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Level 3 section plan");
    await expect(page.locator(".ws-canvas canvas").first()).toBeVisible();
    // captured, prepared and proposed content are distinguished in the list and the legend of states
    await expect(page.getByRole("button", { name: /Refuge chamber/ })).toBeVisible();
    await expect(page.getByText("Domain model prepared")).toBeVisible();
    await expect(page.getByText("Model calibrated against observations")).toBeVisible();

    // selecting an object shows its provenance
    await page.getByRole("button", { name: /Refuge chamber/ }).click();
    await expect(page.locator(".ws-right")).toContainText("Provenance");

    // playback advances engine-computed time
    const time = page.getByRole("slider", { name: "Time" });
    await expect(time).toBeEnabled();
    const before = Number(await time.inputValue());
    await page.getByRole("button", { name: "Play" }).click();
    await expect.poll(async () => Number(await time.inputValue()), { timeout: 10_000 }).toBeGreaterThan(before + 1);
    await page.getByRole("button", { name: "Pause" }).click();
    await expect(page.getByText("Engine-computed trajectory")).toBeVisible();
    void errors;
  });

  test("a run shows its provenance, native cross-checks, metrics and a table for every chart", async ({ page, demos, api, errors }) => {
    const pid = demos.mine_evacuation.id;
    const runs = await api<{ items: Run[] }>(`/projects/${pid}/runs`);
    const run = runs.items.find((r) => r.state === "completed")!;
    await open(page, `/p/${pid}/runs/${run.id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Saved result");
    await expect(page.getByText(/jupedsim 1\.4\.2/)).toBeVisible();
    await expect(page.getByText("Native cross-checks")).toBeVisible();
    await expect(page.locator(".badge", { hasText: "Passed" }).first()).toBeVisible();
    await expect(page.getByText("evac.t100", { exact: true })).toBeVisible();

    const chart = page.getByRole("button", { name: "Show table" }).first();
    await chart.click();
    await expect(page.getByRole("button", { name: "Show chart" }).first()).toBeVisible();
    await expect(page.locator("table").filter({ hasText: "Time since alarm (s)" })).toBeVisible();
    void errors;
  });

  test("the saved comparison is controlled and reports differences against the baseline", async ({ page, demos, api, errors }) => {
    const pid = demos.mine_evacuation.id;
    const comps = await api<Comparison[]>(`/projects/${pid}/comparisons`);
    expect(comps.length).toBeGreaterThan(0);
    await open(page, `/p/${pid}/compare`);
    await expect(page.getByText("Controlled comparison: only the scenario changes differ.")).toBeVisible();
    const t100 = page.locator("tr", { hasText: "evac.t100" });
    await expect(t100).toBeVisible();
    await expect(t100).toContainText("50.22"); // the baseline's recorded T100
    void errors;
  });

  test("the engine library reports real availability without simulating licensed engines", async ({ page, errors }) => {
    await open(page, "/engines");
    await expect(page.getByRole("heading", { name: "JuPedSim" })).toBeVisible();
    await expect(page.locator(".card", { hasText: "Ventsim" })).toContainText(/licence required/i);
    await expect(page.locator(".card", { hasText: "Eclipse SUMO" })).toContainText("Ready");
    void errors;
  });
});
