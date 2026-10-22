"""SimPy runner — executed inside the isolated simpy runtime.

Usage: python -I simpy_runner.py <input_dir> <output_dir>

All stochastic quantities (arrival times, step durations) are pre-sampled by the
adapter and supplied explicitly; this runner only executes the event logic:
entities arrive, then for each step request the step's resource (if any), hold
it for the step duration and release it.
Writes events.csv (one row per resource request) and entities.csv.
"""

import csv
import json
import sys
import time
from pathlib import Path

import simpy


def run_replication(rep: dict, capacities: dict, horizon: float, req_w, ent_w) -> None:
    env = simpy.Environment()
    resources = {rid: simpy.Resource(env, capacity=cap) for rid, cap in capacities.items()}

    def entity(e):
        yield env.timeout(e["arrival_s"])
        for st in e["steps"]:
            rid = st["resource"]
            if rid is None:
                yield env.timeout(st["duration_s"])
                continue
            rec = {"request": env.now, "grant": None, "release": None}
            e["_open"].append((st, rec))
            with resources[rid].request() as req:
                yield req
                rec["grant"] = env.now
                yield env.timeout(st["duration_s"])
                rec["release"] = env.now
        e["_finish"] = env.now

    for e in rep["entities"]:
        e["_open"] = []
        e["_finish"] = None
        if e["arrival_s"] <= horizon:
            env.process(entity(e))
    env.run(until=horizon)
    for e in rep["entities"]:
        if e["arrival_s"] > horizon:
            continue
        for st, rec in e["_open"]:
            req_w.writerow(
                [
                    rep["replication"],
                    e["id"],
                    e["flow"],
                    st["step"],
                    st["resource"],
                    capacities[st["resource"]],
                    rec["request"],
                    "" if rec["grant"] is None else rec["grant"],
                    "" if rec["release"] is None else rec["release"],
                ]
            )
        ent_w.writerow(
            [
                rep["replication"],
                e["id"],
                e["flow"],
                e["arrival_s"],
                "" if e["_finish"] is None else e["_finish"],
            ]
        )


def main(inp: Path, out: Path) -> int:
    spec = json.loads((inp / "runner_input.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(out / "events.csv", "w", newline="") as rf, open(out / "entities.csv", "w", newline="") as ef:
        req_w, ent_w = csv.writer(rf), csv.writer(ef)
        req_w.writerow(
            [
                "replication",
                "entity",
                "flow",
                "step",
                "resource",
                "capacity",
                "request_s",
                "grant_s",
                "release_s",
            ]
        )
        ent_w.writerow(["replication", "entity", "flow", "arrival_s", "finish_s"])
        n = len(spec["replications"])
        for i, rep in enumerate(spec["replications"]):
            run_replication(rep, spec["capacities"], float(spec["horizon_s"]), req_w, ent_w)
            print(f"PROGRESS {(i + 1) / n:.3f} replication {i + 1}/{n}", flush=True)
    (out / "runner_result.json").write_text(
        json.dumps(
            {
                "engine": "simpy",
                "engine_version": simpy.__version__,
                "replications": len(spec["replications"]),
                "wall_time_s": round(time.time() - t0, 3),
                "python": sys.version.split()[0],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
