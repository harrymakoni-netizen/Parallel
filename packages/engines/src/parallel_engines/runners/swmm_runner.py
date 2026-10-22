"""SWMM runner — executed inside the isolated swmm runtime (swmm-toolkit + pyswmm).

Usage: python -I swmm_runner.py <input_dir> <output_dir>

Runs model.inp step-wise (progress heartbeats), then exports the binary output
(model.out) into plain CSV tables for normalisation. The native .rpt and .out
files are kept as raw outputs.
"""

import csv
import json
import shutil
import sys
import time
from pathlib import Path

from pyswmm import Output, Simulation
from swmm.toolkit import solver
from swmm.toolkit.shared_enum import LinkAttribute, NodeAttribute, SubcatchAttribute, SystemAttribute


def _version() -> str:
    v = solver.swmm_get_version()
    s = str(v)
    return f"{s[0]}.{s[1]}.{int(s[2:])}" if len(s) >= 4 else s


def main(inp: Path, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    model = out / "model.inp"
    shutil.copy(inp / "model.inp", model)
    t0 = time.time()
    last = -1.0
    try:
        with Simulation(str(model)) as sim:
            for _ in sim:
                p = sim.percent_complete
                if p - last >= 0.02:
                    print(f"PROGRESS {p:.3f} simulated to {sim.current_time}", flush=True)
                    last = p
    except Exception as exc:  # SWMM input errors are deterministic model errors
        print(f"SWMM error: {exc}")
        rpt = out / "model.rpt"
        if rpt.exists():
            print(rpt.read_text(errors="replace")[-3000:])
        return 2
    with Output(str(out / "model.out")) as o:
        times = list(o.times)  # end of each reporting period
        t_start = o.rpt_start
        if o.units.get("flow") != "CMS":
            print(f"unexpected flow units {o.units}")
            return 2

        def secs(dt):
            return (dt - t_start).total_seconds()

        with open(out / "nodes.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "node", "depth_m", "head_m", "flooding_cms", "total_inflow_cms"])
            for node in o.nodes:
                d = o.node_series(node, NodeAttribute.INVERT_DEPTH)
                h = o.node_series(node, NodeAttribute.HYDRAULIC_HEAD)
                fl = o.node_series(node, NodeAttribute.FLOODING_LOSSES)
                ti = o.node_series(node, NodeAttribute.TOTAL_INFLOW)
                for t in times:
                    w.writerow([secs(t), node, d[t], h[t], fl[t], ti[t]])
        with open(out / "links.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "link", "flow_cms", "depth_m", "velocity_ms", "capacity"])
            for link in o.links:
                q = o.link_series(link, LinkAttribute.FLOW_RATE)
                d = o.link_series(link, LinkAttribute.FLOW_DEPTH)
                v = o.link_series(link, LinkAttribute.FLOW_VELOCITY)
                c = o.link_series(link, LinkAttribute.CAPACITY)
                for t in times:
                    w.writerow([secs(t), link, q[t], d[t], v[t], c[t]])
        with open(out / "subcatchments.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "subcatchment", "rainfall_mm_h", "runoff_cms"])
            for sc in o.subcatchments:
                r = o.subcatch_series(sc, SubcatchAttribute.RAINFALL)
                q = o.subcatch_series(sc, SubcatchAttribute.RUNOFF_RATE)
                for t in times:
                    w.writerow([secs(t), sc, r[t], q[t]])
        with open(out / "system.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                ["t_s", "rainfall_mm_h", "runoff_cms", "flooding_cms", "outfall_cms", "volume_stored_m3"]
            )
            r = o.system_series(SystemAttribute.RAINFALL)
            q = o.system_series(SystemAttribute.RUNOFF_FLOW)
            fl = o.system_series(SystemAttribute.FLOOD_LOSSES)
            of = o.system_series(SystemAttribute.OUTFALL_FLOWS)
            vs = o.system_series(SystemAttribute.VOLUME_STORED)
            for t in times:
                w.writerow([secs(t), r[t], q[t], fl[t], of[t], vs[t]])
        report_step = o.rpt_step
    res = {
        "engine": "swmm",
        "engine_version": _version(),
        "report_step_s": report_step,
        "periods": len(times),
        "wall_time_s": round(time.time() - t0, 3),
        "python": sys.version.split()[0],
    }
    (out / "runner_result.json").write_text(json.dumps(res, indent=2))
    print("PROGRESS 1.0 done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
