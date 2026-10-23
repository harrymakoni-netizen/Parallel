import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against a running Parallel deployment with the demonstration projects seeded.
 *
 *   PARALLEL_DEMO_PASSWORD=... uv run python -m parallel_api.demo seed   # once, from the repository root
 *   E2E_PASSWORD=... npm run e2e
 *
 * E2E_BASE_URL defaults to the Vite development server; point it at a deployed web app to test that.
 * WebGL runs on SwiftShader so the 3D workspace renders without a GPU.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
    launchOptions: {
      executablePath: process.env.E2E_CHROMIUM ?? undefined,
      args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"],
    },
  },
  projects: [
    { name: "dark", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, colorScheme: "dark" } },
    { name: "light", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, colorScheme: "light" }, testMatch: /a11y\.spec\.ts/ },
  ],
});
