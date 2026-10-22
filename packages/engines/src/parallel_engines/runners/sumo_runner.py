"""SUMO runner — executed inside the isolated sumo runtime (eclipse-sumo + traci).

Usage: python -I sumo_runner.py <input_dir> <output_dir>

1. netconvert builds the network from Parallel's plain XML (nodes, edges, tls).
2. SUMO is started under TraCI and stepped to the horizon (or until no vehicles
   remain), emitting progress for heartbeats.
3. Native outputs (tripinfo incl. unfinished, statistics, summary, queues, fcd)
   are written to the output directory for normalisation.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import sumo
import traci


def main(inp: Path, out: Path) -> int:
    spec = json.loads((inp / "runner_input.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    home = Path(sumo.SUMO_HOME)
    netconvert = home / "bin" / "netconvert"
    sumo_bin = home / "bin" / "sumo"
    ver = subprocess.run([str(sumo_bin), "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    nc_args = [
        str(netconvert),
        "--node-files",
        str(inp / "net.nod.xml"),
        "--edge-files",
        str(inp / "net.edg.xml"),
        "--output-file",
        str(out / "net.net.xml"),
        "--tls.guess",
        "false",
        "--junctions.join",
        "false",
        "--no-warnings",
        "false",
    ]
    if (inp / "net.tll.xml").exists():
        nc_args += ["--tllogic-files", str(inp / "net.tll.xml")]
    print("PROGRESS 0.02 building network with netconvert", flush=True)
    nc = subprocess.run(nc_args, capture_output=True, text=True)
    (out / "netconvert.log").write_text(nc.stdout + nc.stderr)
    if nc.returncode != 0:
        print(nc.stdout + nc.stderr)
        return 2
    end = float(spec["end_s"])
    args = [
        str(sumo_bin),
        "--net-file",
        str(out / "net.net.xml"),
        "--route-files",
        str(inp / "routes.rou.xml"),
        "--begin",
        "0",
        "--end",
        str(end),
        "--step-length",
        str(spec["step_length_s"]),
        "--seed",
        str(spec["seed"]),
        "--time-to-teleport",
        "-1",
        "--ignore-route-errors",
        "true",
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
        "--tripinfo-output",
        str(out / "tripinfo.xml"),
        "--tripinfo-output.write-unfinished",
        "true",
        "--statistic-output",
        str(out / "statistics.xml"),
        "--summary-output",
        str(out / "summary.xml"),
        "--queue-output",
        str(out / "queue.xml"),
        "--queue-output.period",
        str(spec["queue_period_s"]),
        "--fcd-output",
        str(out / "fcd.xml"),
        "--device.fcd.period",
        str(spec["fcd_period_s"]),
        "--error-log",
        str(out / "sumo-errors.log"),
    ]
    t0 = time.time()
    traci.start(args)
    api = traci.getVersion()
    last = -1.0
    try:
        while True:
            t = traci.simulation.getTime()
            if t >= end - 1e-9:
                break
            if t > 0 and traci.simulation.getMinExpectedNumber() == 0 and t >= spec["last_depart_s"]:
                break
            traci.simulationStep()
            frac = min(1.0, traci.simulation.getTime() / end)
            if frac - last >= 0.02:
                print(
                    f"PROGRESS {frac:.3f} t={traci.simulation.getTime():.0f}s running="
                    f"{traci.vehicle.getIDCount()}",
                    flush=True,
                )
                last = frac
        stop_time = traci.simulation.getTime()
    finally:
        traci.close()
    res = {
        "engine": "sumo",
        "engine_version": ver.replace("Eclipse SUMO sumo ", "").strip(),
        "version_banner": ver,
        "traci_api": list(api),
        "seed": spec["seed"],
        "end_s": end,
        "stopped_at_s": stop_time,
        "wall_time_s": round(time.time() - t0, 3),
        "sumo_home": os.path.basename(str(home)),
    }
    (out / "runner_result.json").write_text(json.dumps(res, indent=2))
    print("PROGRESS 1.0 done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
