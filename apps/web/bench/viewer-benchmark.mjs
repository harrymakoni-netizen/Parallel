// Viewer benchmark (spec 30.5): scene loading, interaction and large-result playback in a real browser.
// Run after scripts/benchmark.py, against a deployment with the demonstrations seeded:
//   E2E_PASSWORD=... node bench/viewer-benchmark.mjs        (writes ../../docs/benchmarks/viewer.json)
// Uses the same E2E_* settings as the end-to-end tests. Frame rates on a host without a GPU come from
// software WebGL (SwiftShader) and are a lower bound.
import { chromium } from "@playwright/test";
import { writeFileSync } from "node:fs";

const BASE = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";
const EMAIL = process.env.E2E_EMAIL ?? "presenter@parallel-demo.example";
const PASSWORD = process.env.E2E_PASSWORD;
const REPEATS = 3;
if (!PASSWORD) throw new Error("Set E2E_PASSWORD");

const median = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
const fmt = (xs, unit = "s", div = 1000) => {
  const v = xs.map((x) => x / div);
  return `${median(v).toFixed(2)} ${unit} median (${Math.min(...v).toFixed(2)} to ${Math.max(...v).toFixed(2)}, n = ${v.length})`;
};

const browser = await chromium.launch({
  executablePath: process.env.E2E_CHROMIUM || undefined,
  args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"],
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
const login = await ctx.request.post(`${BASE}/api/v1/auth/login`, { data: { email: EMAIL, password: PASSWORD } });
const token = (await login.json()).access_token;
const api = async (path) => (await ctx.request.get(`${BASE}/api/v1${path}`, { headers: { Authorization: `Bearer ${token}` } })).json();
await ctx.addInitScript((t) => localStorage.setItem("parallel.token", t), token);
const page = await ctx.newPage();

const projects = Object.fromEntries((await api("/projects?limit=100")).items.filter((p) => p.demo_key).map((p) => [p.demo_key, p.id]));
const runsOf = async (pid) => (await api(`/projects/${pid}/runs?limit=100`)).items.filter((r) => r.state === "completed");
const sumo = (await runsOf(projects.urban_planning)).find((r) => r.engine_id === "sumo");
const crowd = (await runsOf(projects.mine_evacuation)).find((r) => (r.label ?? "").startsWith("Benchmark: 160 people"));
if (!crowd) throw new Error("run scripts/benchmark.py first (it records the 160-person run)");
const playbackOf = async (run) => {
  const arts = await api(`/runs/${run.id}/artifacts`);
  const pb = arts.find((a) => a.name === "playback.json");
  const url = (await api(`/runs/${run.id}/artifacts/${pb.id}/url`)).url;
  const data = await (await ctx.request.get(url.startsWith("http") ? url : `${BASE}${url}`)).json();
  const positions = data.frames.reduce((n, f) => n + f.i.length, 0);
  return { mb: pb.size_bytes / 1e6, frames: data.frames.length, agents: data.agents.length, positions,
           seconds: data.frames.at(-1).t - data.frames[0].t };
};

// frames per second while `during` runs
const fps = async (ms, during = async () => {}) => {
  const counting = page.evaluate((ms) => new Promise((res) => {
    let n = 0;
    const t0 = performance.now();
    const f = () => { n++; if (performance.now() - t0 < ms) requestAnimationFrame(f); else res((n * 1000) / (performance.now() - t0)); };
    requestAnimationFrame(f);
  }), ms);
  await during();
  return counting;
};

const timeTo = async (url, ready) => {
  const t0 = Date.now();
  await page.goto(`${BASE}${url}`);
  await ready();
  return Date.now() - t0;
};

const rows = [];
const renderer = await (async () => {
  await page.goto(`${BASE}/projects`);
  return page.evaluate(() => {
    const gl = document.createElement("canvas").getContext("webgl2");
    const ext = gl && gl.getExtension("WEBGL_debug_renderer_info");
    return gl ? (ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)) : "no WebGL 2";
  });
})();

// 1. scene workspace load: prepared plan scene (mine section, 13 objects)
const planLoads = [];
for (let i = 0; i < REPEATS; i++) {
  planLoads.push(await timeTo(`/p/${projects.mine_evacuation}/scene`, async () => {
    await page.locator(".ws-canvas canvas").first().waitFor();
    await page.getByRole("button", { name: /Refuge chamber/ }).waitFor();
  }));
}
rows.push({ measure: "Open the scene workspace: prepared mine plan (13 objects), navigation to interactive", result: fmt(planLoads) });

// 2. captured scene with a reconstructed point cloud
const cloudLoads = [];
for (let i = 0; i < REPEATS; i++) {
  cloudLoads.push(await timeTo(`/p/${projects.crashlens}/scene`, async () => {
    await page.getByRole("checkbox", { name: /Sparse point cloud/ }).waitFor({ timeout: 60_000 });
  }));
}
const points = await page.getByText(/\d+ points, mean reprojection error/).first().innerText();
rows.push({ measure: `Open a reconstructed scene and load its point cloud (${points.split(",")[0]})`, result: fmt(cloudLoads) });

// 3. interaction: orbiting the point-cloud scene with the mouse
const box = await page.locator(".ws-canvas").first().boundingBox();
const orbit = await fps(3000, async () => {
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  for (let k = 0; k < 60; k++) await page.mouse.move(box.x + box.width / 2 + k * 4, box.y + box.height / 2 + Math.sin(k / 6) * 30, { steps: 2 });
  await page.mouse.up();
});
rows.push({ measure: "Orbit the reconstructed scene (frame rate while dragging)", result: `${orbit.toFixed(1)} frames/s` });

// 4. large-result playback: an hour of SUMO vehicles, and the congested 160-person evacuation
for (const [label, pid, run, speed] of [["SUMO corridor, 1 h of traffic", projects.urban_planning, sumo, "16"],
                                        ["JuPedSim, 160 people", projects.mine_evacuation, crowd, "4"]]) {
  const pb = await playbackOf(run);
  const loads = [];
  for (let i = 0; i < REPEATS; i++) {
    loads.push(await timeTo(`/p/${pid}/scene?run=${run.id}`, async () => {
      await page.getByRole("slider", { name: "Time" }).and(page.locator(":enabled")).waitFor({ timeout: 60_000 });
    }));
  }
  await page.locator("#pb-speed").selectOption(speed);
  const playing = await fps(5000, async () => { await page.getByRole("button", { name: "Play" }).click(); });
  await page.getByRole("button", { name: "Pause" }).click().catch(() => {});
  rows.push({
    measure: `Playback ${label}: ${pb.frames} frames, ${pb.agents} agents, ${pb.positions.toLocaleString("en-GB")} positions, ` +
             `${pb.mb.toFixed(2)} MB playback derivative; load until playable`,
    result: fmt(loads),
  });
  rows.push({ measure: `Playback ${label} at ${speed}x (frame rate while playing)`, result: `${playing.toFixed(1)} frames/s` });
}

const out = { recorded_at: new Date().toISOString(), browser: `Chromium ${browser.version()}`, renderer,
              viewport: "1440 x 900", base_url: BASE, rows };
writeFileSync(new URL("../../../docs/benchmarks/viewer.json", import.meta.url), JSON.stringify(out, null, 1) + "\n");
console.log(JSON.stringify(out, null, 1));
await browser.close();
