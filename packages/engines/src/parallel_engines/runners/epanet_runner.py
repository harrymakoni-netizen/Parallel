"""EPANET runner — executed inside the isolated epanet runtime (WNTR bundling the EPANET 2.2 toolkit).

Usage: python -I epanet_runner.py <input_dir> <output_dir>

Builds the network with WNTR from runner_input.json (SI units), runs the EPANET
2.2 solver (EpanetSimulator), keeps the native .inp/.rpt/.bin outputs and
exports node/link result tables as CSV.
"""

import json
import sys
import time
from pathlib import Path

import wntr


def main(inp: Path, out: Path) -> int:
    spec = json.loads((inp / "runner_input.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    wn = wntr.network.WaterNetworkModel()
    wn.options.hydraulic.headloss = "H-W"
    wn.options.hydraulic.inpfile_units = "LPS"
    wn.options.time.duration = spec["duration_s"]
    wn.options.time.hydraulic_timestep = spec["hydraulic_step_s"]
    wn.options.time.pattern_timestep = spec["pattern_step_s"]
    wn.options.time.report_timestep = spec["report_step_s"]
    for p in spec["patterns"]:
        wn.add_pattern(p["id"], p["multipliers"])
    for j in spec["junctions"]:
        wn.add_junction(
            j["id"],
            base_demand=j["base_demand_m3s"],
            demand_pattern=j["pattern_id"],
            elevation=j["elevation_m"],
            coordinates=tuple(j["position"][:2]),
        )
        if j.get("extra_demand_m3s"):
            # scenario demand (e.g. fire flow): constant, no pattern
            wn.get_node(j["id"]).add_demand(j["extra_demand_m3s"], None, "scenario")
    for r in spec["reservoirs"]:
        wn.add_reservoir(r["id"], base_head=r["head_m"], coordinates=tuple(r["position"][:2]))
    for t in spec["tanks"]:
        wn.add_tank(
            t["id"],
            elevation=t["elevation_m"],
            init_level=t["init_level_m"],
            min_level=t["min_level_m"],
            max_level=t["max_level_m"],
            diameter=t["diameter_m"],
            coordinates=tuple(t["position"][:2]),
        )
    for p in spec["pipes"]:
        wn.add_pipe(
            p["id"],
            p["node1"],
            p["node2"],
            length=p["length_m"],
            diameter=p["diameter_m"],
            roughness=p["roughness_hw"],
            minor_loss=p["minor_loss"],
            initial_status="CLOSED" if p["status"] == "CLOSED" else "OPEN",
            check_valve=p["status"] == "CV",
        )
    for pu in spec["pumps"]:
        wn.add_curve(f"{pu['id']}_curve", "HEAD", [tuple(x) for x in pu["curve_m3s_m"]])
        wn.add_pump(
            pu["id"],
            pu["node1"],
            pu["node2"],
            pump_type="HEAD",
            pump_parameter=f"{pu['id']}_curve",
            speed=pu["speed"],
        )
    print("PROGRESS 0.2 network built", flush=True)
    t0 = time.time()
    try:
        results = wntr.sim.EpanetSimulator(wn).run_sim(file_prefix=str(out / "model"), version=2.2)
    except Exception as exc:
        print(f"EPANET error: {exc}")
        return 2
    print("PROGRESS 0.9 solved", flush=True)
    pressure = results.node["pressure"]
    demand = results.node["demand"]
    head = results.node["head"]
    flow = results.link["flowrate"]
    velocity = results.link["velocity"]
    with open(out / "nodes.csv", "w") as f:
        f.write("t_s,node,pressure_m,head_m,demand_m3s\n")
        for t in pressure.index:
            for n in pressure.columns:
                f.write(
                    f"{t},{n},{float(pressure.at[t, n])!r},{float(head.at[t, n])!r},{float(demand.at[t, n])!r}\n"
                )
    with open(out / "links.csv", "w") as f:
        f.write("t_s,link,flow_m3s,velocity_ms\n")
        for t in flow.index:
            for lk in flow.columns:
                f.write(f"{t},{lk},{float(flow.at[t, lk])!r},{float(velocity.at[t, lk])!r}\n")
    res = {
        "engine": "epanet",
        "engine_version": "EPANET 2.2 (WNTR EpanetSimulator)",
        "wntr_version": wntr.__version__,
        "report_steps": len(pressure.index),
        "wall_time_s": round(time.time() - t0, 3),
        "node_types": {n: wn.get_node(n).node_type for n in wn.node_name_list},
    }
    (out / "runner_result.json").write_text(json.dumps(res, indent=2))
    print("PROGRESS 1.0 done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
