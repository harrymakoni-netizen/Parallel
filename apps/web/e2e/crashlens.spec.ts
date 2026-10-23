import { expect, open, test } from "./support";

test.describe("CrashLens", () => {
  test("evidence is hashed at receipt and still verifies; observations and interpretations stay separate", async ({ page, demos, errors }) => {
    await open(page, `/p/${demos.crashlens.id}/evidence`);
    const items = page.locator(".card .card", { has: page.getByText(/^SHA-256 [0-9a-f]{64}$/) }); // one card per evidence item
    await expect(items.first()).toBeVisible();
    await items.first().getByRole("button", { name: "Verify hash" }).click();
    await expect(page.getByText("Unchanged since receipt")).toBeVisible();

    await expect(page.getByRole("heading", { name: "Observations" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Interpretations" })).toBeVisible();
    // an interpretation cites the observations it reasons from and states its limitation
    const interp = page.locator(".card", { has: page.getByRole("heading", { name: "Interpretations" }) });
    await expect(interp).toContainText("Based on:");
    await expect(interp).toContainText("Not a determination of fault");
    void errors;
  });

  test("hypotheses are compared by evidence links and residuals, never by probability", async ({ page, demos, errors }) => {
    await open(page, `/p/${demos.crashlens.id}/hypotheses`);
    await expect(page.getByText("Parallel never computes a probability that a hypothesis is true, and never determines fault.")).toBeVisible();
    const table = page.locator("table").first();
    await expect(table).toContainText("Supporting");
    await expect(table).toContainText("RMS residual");
    // no collision engine is installed, so no residual is invented
    await expect(table).toContainText("not evaluated");
    void errors;
  });

  test("the reconstructed scene loads its point cloud and shows the reconstruction's checks", async ({ page, demos, errors }) => {
    await page.goto(`/p/${demos.crashlens.id}/scene`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Training road scene");
    await expect(page.getByRole("checkbox", { name: /Sparse point cloud/ })).toBeChecked();
    await expect(page.getByText(/frames registered/)).toBeVisible();
    await expect(page.getByText(/independent distance checks/)).toBeVisible();
    await expect(page.getByText(/Within/).first()).toBeVisible();
    void errors;
  });
});
