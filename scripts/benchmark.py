"""Benchmark workloads (spec 30.5), run for real against the configured deployment with the
demonstration projects seeded:

    uv run python scripts/benchmark.py            # writes docs/benchmarks/engines.json
    (cd apps/web && node bench/viewer-benchmark.mjs)   # writes docs/benchmarks/viewer.json
    uv run python scripts/benchmark.py --summary  # writes docs/benchmarks.md from both
    uv run python -m parallel_api.demo reset      # restores the demonstrations afterwards

Workloads run in process with the real API, engines and storage (tasks execute eagerly, so queue
latency is excluded and stated). Every engine workload is repeated; the median and range are
reported with the host's hardware. These are benchmark workloads, not promised performance figures.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("PARALLEL_TASK_ALWAYS_EAGER", "1")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "benchmarks"
REPEATS = 3


def hardware() -> dict[str, Any]:
    cpu = next((ln.split(":", 1)[1].strip() for ln in Path("/proc/cpuinfo").read_text().splitlines()
                if ln.startswith("model name")), platform.processor())
    mem = next((int(ln.split()[1]) for ln in Path("/proc/meminfo").read_text().splitlines()
                if ln.startswith("MemTotal")), 0)
    return {"cpu": cpu, "logical_cpus": os.cpu_count(), "memory_gb": round(mem / 1024**2, 1),
            "os": f"{platform.system()} {platform.release()}", "python": platform.python_version(),
            "gpu": "none (CPU only)"}


def _seconds(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()


def stages(run: dict) -> dict[str, float]:
    """Seconds spent in each run state, from the run's recorded state history."""
    h = run["state_history"]
    return {x["state"]: round(_seconds(x["at"], y["at"]), 3) for x, y in zip(h, h[1:], strict=False)}


def summary(samples: list[float]) -> dict[str, float]:
    return {"median_s": round(statistics.median(samples), 2), "min_s": round(min(samples), 2),
            "max_s": round(max(samples), 2), "n": len(samples)}


def main_engines() -> int:
    from fastapi.testclient import TestClient

    from parallel_api import demo
    from parallel_api.main import create_app

    ctx = demo.ensure_org_and_users()
    results: dict[str, Any] = {"recorded_at": datetime.now(UTC).isoformat(), "hardware": hardware(),
                               "mode": "in-process API, tasks executed eagerly (queue latency excluded)",
                               "workloads": {}}
    with TestClient(create_app()) as client:
        api = demo.Api(client, ctx["token"])
        projects = {p["demo_key"]: p["id"] for p in api.get("/projects?limit=100")["items"] if p.get("demo_key")}
        missing = set(demo.DEMO_KEYS) - set(projects)
        if missing:
            print(f"seed the demonstrations first (missing {sorted(missing)})", file=sys.stderr)
            return 1

        def scenarios(pid: str) -> list[dict]:
            return api.get(f"/projects/{pid}/scenarios")

        def run_rev(pid: str, rev: str, label: str) -> tuple[float, dict]:
            t0 = time.perf_counter()
            r = api.post(f"/projects/{pid}/runs", headers={"Idempotency-Key": f"bench-{rev}-{time.time_ns()}"},
                         json={"scenario_revision_id": rev, "label": f"Benchmark: {label}"})
            wall = time.perf_counter() - t0
            if r["state"] != "completed":
                raise RuntimeError(f"{label}: run ended {r['state']}: {r.get('error_message')}")
            return wall, r

        def engine_workload(key: str, pid: str, rev: str, label: str, describe: dict) -> dict:
            walls, runs = [], []
            for i in range(REPEATS):
                w, r = run_rev(pid, rev, f"{label} #{i + 1}")
                walls.append(w)
                runs.append(r)
            arts = api.get(f"/runs/{runs[-1]['id']}/artifacts")
            out = {**describe, "engine": runs[-1]["engine_version"], "end_to_end": summary(walls),
                   "engine_execution": summary([stages(r).get("running", 0.0) for r in runs]),
                   "stages_last_run_s": stages(runs[-1]),
                   "outputs_mb": round(sum(a["size_bytes"] for a in arts) / 1e6, 2),
                   "playback_mb": round(sum(a["size_bytes"] for a in arts if a["name"] == "playback.json") / 1e6, 2),
                   "run_ids": [r["id"] for r in runs]}
            results["workloads"][key] = out
            print(key, json.dumps({k: out[k] for k in ("end_to_end", "engine_execution")}))
            return out

        # pedestrian: the mine section with about 100 and with 300 people
        mine = projects["mine_evacuation"]
        base = next(s for s in scenarios(mine) if s["is_baseline"])
        model = api.get(f"/models/{base['domain_model_id']}")
        groups = api.get(f"/model-revisions/{model['current_revision_id']}")["content"]["content"]["start_groups"]
        total = sum(g["count"] for g in groups)
        from parallel_engines import reference_models as rm

        pre = rm.premovement(5, 15).model_dump(mode="json")
        # 100 people in proportion to the crews; 160 is close to what the start zones hold at the engine's
        # 0.55 m placement spacing (it refuses to place more), giving a congested case
        for people, key in ((100, "pedestrian_100"), (160, "pedestrian_160_congested")):
            if people == 160:
                counts = {"face_crew": 60, "workshop_crew": 60, "development_crew": 40}
            else:
                counts = {g["id"]: round(g["count"] * people / total) for g in groups}
                counts[groups[0]["id"]] += people - sum(counts.values())
            rev = demo.scenario(api, mine, base["domain_model_id"], f"Benchmark: {people} people",
                                demo.scen("pedestrian", "jupedsim",
                                          {"kind": "pedestrian", "pre_movement": pre, "population_overrides": counts},
                                          seed=42, description=f"Benchmark workload: {people} people in the level section."))
            engine_workload(key, mine, rev, f"{people} people",
                            {"workload": f"JuPedSim evacuation of a level mine section, {people} agents, "
                                         "dt 0.01 s, CollisionFreeSpeedModel", "agents": people})

        # traffic and drainage: the demonstration baselines, re-run
        for key, demo_key, text in (
            ("traffic_small_network", "urban_planning",
             "SUMO, priority junction corridor (4 approaches, 30 network objects), 1 h demand"),
            ("drainage_small_network", "urban_drainage",
             "SWMM, 4 junctions, 4 conduits, 1 outfall, 60 min design storm"),
            ("operations_des", "construction",
             "SimPy, site delivery operations, 10 replications with common random numbers"),
        ):
            pid = projects[demo_key]
            s = next(x for x in scenarios(pid) if x["is_baseline"])
            engine_workload(key, pid, s["current_revision_id"], s["name"], {"workload": text})

        # report generation: a three-run comparison report with charts, tables and provenance
        comp = api.get(f"/projects/{mine}/comparisons")[0]
        walls = []
        for i in range(REPEATS):
            t0 = time.perf_counter()
            rep = api.post(f"/projects/{mine}/reports", json={"kind": "comparison", "title": f"Benchmark report #{i + 1}",
                                                              "comparison_id": comp["id"]})
            walls.append(time.perf_counter() - t0)
            if rep["status"] != "completed":
                raise RuntimeError(f"report ended {rep['status']}: {rep.get('error')}")
        results["workloads"]["report_generation"] = {
            "workload": "PDF, CSV and JSON report of a three-run comparison (charts with table twins, provenance)",
            "end_to_end": summary(walls)}
        print("report_generation", results["workloads"]["report_generation"]["end_to_end"])

        # reconstruction: synthetic photographs through the real pipeline (COLMAP on CPU)
        from parallel_reconstruction.synthetic import generate, road_scene

        crash = projects["crashlens"]
        for frames in (36, 72):
            with tempfile.TemporaryDirectory() as td:
                gt = generate(road_scene(), Path(td), n_frames=frames, width=560, height_px=420, video=False)
                batch = api.post(f"/projects/{crash}/capture-batches",
                                 json={"name": f"Benchmark capture ({frames} frames)", "kind": "photos"})
                for name in gt.frame_names:
                    api.post(f"/projects/{crash}/assets", data={"capture_batch_id": batch["id"]},
                             files={"file": (name, (Path(td) / "frames" / name).read_bytes(), "image/png")})
                buf = io.StringIO("frame,x,y,z\n" + "".join(
                    f"{n},{c[0]},{c[1]},{c[2]}\n" for n, c in zip(gt.frame_names, gt.camera_centres, strict=True)))
                api.post(f"/projects/{crash}/assets", data={"capture_batch_id": batch["id"]},
                         files={"file": ("camera_positions.csv", buf.getvalue().encode(), "text/csv")})
            t0 = time.perf_counter()
            job = api.post(f"/projects/{crash}/reconstructions", json={
                "capture_batch_id": batch["id"],
                "settings": {"scene_name": f"Benchmark ({frames} frames)", "tolerance_abs_m": 0.10,
                             "reference_source": "synthetic surveyed camera positions"}})
            wall = time.perf_counter() - t0
            if job["status"] != "completed":
                raise RuntimeError(f"reconstruction {job['status']}: {job.get('error')}")
            per_stage = {s["name"]: round(_seconds(s["started_at"], s["finished_at"]), 2)
                         for s in job["stages"] if s.get("started_at") and s.get("finished_at")}
            results["workloads"][f"reconstruction_{frames}_frames"] = {
                "workload": f"{frames} synthetic photographs (560 x 420 px) of the training road scene; "
                            "COLMAP 3.9.1 sparse reconstruction on CPU, scale alignment, checks, "
                            "point cloud and Gaussian initialisation",
                "frames": frames, "end_to_end_s": round(wall, 2), "stages_s": per_stage}
            print(f"reconstruction_{frames}", round(wall, 1), per_stage)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "engines.json").write_text(json.dumps(results, indent=1) + "\n")
    print(f"wrote {OUT / 'engines.json'}")
    return 0


def write_summary() -> int:
    eng = json.loads((OUT / "engines.json").read_text())
    view = json.loads((OUT / "viewer.json").read_text()) if (OUT / "viewer.json").exists() else None
    h = eng["hardware"]
    w = eng["workloads"]
    lines = [
        "# Benchmarks",
        "",
        "Benchmark workloads from spec section 30.5, measured on real engine runs. **These are benchmark",
        "workloads, not promised performance figures**: they describe one host and one configuration.",
        "Regenerate with `scripts/benchmark.py` (see its docstring); raw results are in",
        "[`benchmarks/engines.json`](benchmarks/engines.json) and [`benchmarks/viewer.json`](benchmarks/viewer.json).",
        "",
        "## Host",
        "",
        f"- Recorded: {eng['recorded_at'][:16].replace('T', ' ')} UTC",
        f"- CPU: {h['cpu']}, {h['logical_cpus']} logical CPUs; memory {h['memory_gb']} GB; GPU: {h['gpu']}",
        f"- OS: {h['os']}; Python {h['python']}",
        f"- Mode: {eng['mode']}. Each engine workload ran {REPEATS} times; median (min to max) shown.",
        "",
        "## Engine workloads",
        "",
        "| Workload | Engine | End to end | Engine execution | Outputs |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key in ("pedestrian_100", "pedestrian_160_congested", "traffic_small_network",
                "drainage_small_network", "operations_des"):
        x = w[key]
        e, r = x["end_to_end"], x["engine_execution"]
        lines.append(f"| {x['workload']} | {x['engine']} | {e['median_s']} s ({e['min_s']} to {e['max_s']}) | "
                     f"{r['median_s']} s ({r['min_s']} to {r['max_s']}) | {x['outputs_mb']} MB |")
    lines += ["", "End to end covers validation, input materialisation, the engine run, result collection,",
              "normalisation, cross-checks and storage of every artifact. Engine execution is the time the",
              "run spent in the `running` state.", "",
              "## Report generation", ""]
    rg = w["report_generation"]
    lines.append(f"{rg['workload']}: {rg['end_to_end']['median_s']} s median "
                 f"({rg['end_to_end']['min_s']} to {rg['end_to_end']['max_s']} s, n = {rg['end_to_end']['n']}).")
    lines += ["", "## Reconstruction", "", "| Workload | End to end | Slowest stages |", "| --- | --- | --- |"]
    for key in ("reconstruction_36_frames", "reconstruction_72_frames"):
        x = w[key]
        top = sorted(x["stages_s"].items(), key=lambda kv: -kv[1])[:3]
        lines.append(f"| {x['workload']} | {x['end_to_end_s']} s | "
                     + ", ".join(f"{k.replace('_', ' ')} {v} s" for k, v in top) + " |")
    lines += ["", "Gaussian splat training needs a GPU worker. This host has none, so the training stage recorded",
              "only the untrained initialisation (structure-from-motion points as isotropic Gaussians), and the",
              "workspace labels it as untrained."]
    if view:
        lines += ["", "## Viewer", "",
                  f"Browser: {view['browser']}, {view['renderer']}, viewport {view['viewport']}, application served",
                  f"from {view['base_url']} (the Vite development server, API on the same host). Software WebGL",
                  "(SwiftShader) on this CPU-only host, so frame rates are a lower bound for a machine with a GPU.",
                  "Measured with `apps/web/bench/viewer-benchmark.mjs`; times run from navigation to the state named.", "",
                  "| Measurement | Result |", "| --- | --- |"]
        for row in view["rows"]:
            lines.append(f"| {row['measure']} | {row['result']} |")
    (ROOT / "docs" / "benchmarks.md").write_text("\n".join(lines) + "\n")
    print("wrote docs/benchmarks.md")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--summary", action="store_true", help="write docs/benchmarks.md from recorded results")
    sys.exit(write_summary() if p.parse_args().summary else main_engines())
