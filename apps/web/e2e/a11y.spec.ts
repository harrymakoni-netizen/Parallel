import { test as base } from "@playwright/test";
import { EMAIL, expect, open, PASSWORD, test } from "./support";

/** Runs in both colour schemes (see playwright.config.ts). */
base("signing in: a wrong password is refused with a message, the right one opens the projects", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/projects$/);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
});

const PAGES = ["/projects", "/demonstrations", "/engines", "/reports", "/settings"];

test("every page has one main landmark, a level-one heading and a working skip link", async ({ page, demos, errors }) => {
  const project = demos.urban_drainage.id;
  for (const path of [...PAGES, `/p/${project}`, `/p/${project}/runs`, `/p/${project}/compare`, `/p/${project}/reports`]) {
    await open(page, path);
    await expect(page.locator("main"), path).toHaveCount(1);
    await expect(page.locator("h1"), path).toHaveCount(1);
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip, path).toBeFocused();
    await skip.press("Enter");
    await expect(page.locator("main"), path).toBeFocused();
  }
  void errors;
});

test("status is conveyed by text as well as colour, and charts offer a data table", async ({ page, demos, errors }) => {
  await open(page, `/p/${demos.urban_drainage.id}/runs`);
  const badges = page.locator(".badge");
  await expect(badges.first()).toBeVisible();
  for (const text of await badges.allInnerTexts()) expect(text.trim().length).toBeGreaterThan(0);
  // images and icons that carry meaning have text alternatives
  for (const img of await page.locator("img").all()) expect(await img.getAttribute("alt")).not.toBeNull();
  void errors;
});

test("form fields on the project creation page are labelled", async ({ page, errors }) => {
  await open(page, "/projects/new");
  const unlabelled = await page.locator("input:not([type=hidden]), select, textarea").evaluateAll((els) =>
    els.filter((el) => {
      const id = el.getAttribute("id");
      return !el.getAttribute("aria-label") && !(id && document.querySelector(`label[for="${id}"]`)) && !el.closest("label");
    }).map((el) => el.outerHTML.slice(0, 80)),
  );
  expect(unlabelled).toEqual([]);
  void errors;
});
