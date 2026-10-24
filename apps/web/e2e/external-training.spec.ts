import { Buffer } from "node:buffer";
import { expect, open, test } from "./support";

test("a reconstruction without a GPU offers external training: both downloads work and a non-splat is refused with its reason", async ({ page, demos }) => {
  await open(page, `/p/${demos.crashlens.id}/captures`);
  await page.getByText("Train the Gaussian splat on a free GPU").first().click();

  const [notebook] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download notebook" }).first().click()]);
  expect(notebook.suggestedFilename()).toBe("parallel_splat_training.ipynb");
  const [pkg] = await Promise.all([
    page.waitForEvent("download", { timeout: 60_000 }),
    page.getByRole("button", { name: "Download training package" }).first().click(),
  ]);
  expect(pkg.suggestedFilename()).toMatch(/^parallel-training-[0-9a-f]+\.zip$/);

  // the import button waits for a file; a file that is not a Gaussian splat is refused and says why
  const importButton = page.getByRole("button", { name: "Import trained splat" }).first();
  await expect(importButton).toBeDisabled();
  await page.locator('input[accept=".ply,.json"]').first().setInputFiles({
    name: "not-a-splat.ply", mimeType: "application/octet-stream", buffer: Buffer.from("this is not a PLY file"),
  });
  await importButton.click();
  await expect(page.getByRole("alert").filter({ hasText: /PLY/ }).first()).toBeVisible();
});
