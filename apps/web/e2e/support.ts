import { expect, test as base, type APIRequestContext, type Page } from "@playwright/test";

export const EMAIL = process.env.E2E_EMAIL ?? "presenter@parallel-demo.example";
export const PASSWORD = process.env.E2E_PASSWORD ?? "";

export interface Demo {
  id: string;
  demo_key: string;
  name: string;
}

async function login(request: APIRequestContext, email = EMAIL, password = PASSWORD): Promise<string> {
  if (!password) throw new Error("Set E2E_PASSWORD to the demonstration presenter's password (see playwright.config.ts).");
  const r = await request.post("/api/v1/auth/login", { data: { email, password } });
  expect(r.status(), `sign-in as ${email}`).toBe(200);
  return (await r.json()).access_token as string;
}

/** Tests run signed in as the presenter, with the API reachable through the same origin. */
export const test = base.extend<{ token: string; api: <T>(path: string) => Promise<T>; demos: Record<string, Demo>; errors: string[] }>({
  token: async ({ request }, use) => use(await login(request)),
  api: async ({ request, token }, use) =>
    use(async <T,>(path: string) => {
      const r = await request.get(`/api/v1${path}`, { headers: { Authorization: `Bearer ${token}` } });
      expect(r.ok(), `GET ${path}`).toBeTruthy();
      return (await r.json()) as T;
    }),
  demos: async ({ api }, use) => {
    const page = await api<{ items: Demo[] }>("/projects?limit=100");
    const byKey = Object.fromEntries(page.items.filter((p) => p.demo_key).map((p) => [p.demo_key, p]));
    expect(Object.keys(byKey).sort(), "the five demonstration projects are seeded").toEqual(
      ["construction", "crashlens", "mine_evacuation", "urban_drainage", "urban_planning"],
    );
    await use(byKey);
  },
  // console errors and uncaught exceptions fail the test that caused them
  errors: async ({ page }, use) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    await use(errors);
    expect(errors, "browser console errors").toEqual([]);
  },
  page: async ({ page, token }, use) => {
    await page.addInitScript((t) => { try { localStorage.setItem("parallel.token", t); } catch { /* storage blocked */ } }, token);
    await use(page);
  },
});

export { expect, login };

export async function open(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await expect(page.locator("main h1").first()).toBeVisible();
}
