"""JuPedSim runner — executed inside the isolated jupedsim runtime.

Usage: python -I jupedsim_runner.py <input_dir> <output_dir>

Reads runner_input.json (materialised by the Parallel adapter), simulates each
connected walkable component as a separate JuPedSim model (JuPedSim requires a
connected walkable polygon), and writes:
  agents.csv          one row per simulated agent: id, status, exit_id, exit_time_s, start_time_s
  trajectories.csv    frame, t_s, agent, x, y (every n steps)
  runner_result.json  engine version and per-component execution facts
Only stdlib + jupedsim + shapely are imported.
"""

import csv
import json
import math
import sys
import time
from pathlib import Path

import jupedsim as jps
import shapely
from shapely import wkt


def main(inp: Path, out: Path) -> int:
    spec = json.loads((inp / "runner_input.json").read_text())
    params = spec["model"]
    dt = float(spec["dt_s"])
    horizon = float(spec["max_duration_s"])
    every = int(spec["trajectory_every_n_steps"])
    out.mkdir(parents=True, exist_ok=True)
    agents_by_comp = {}
    for a in spec["agents"]:
        agents_by_comp.setdefault(a["component"], []).append(a)

    with open(out / "trajectories.csv", "w", newline="") as traj_f:
        traj = csv.writer(traj_f)
        traj.writerow(["component", "frame", "t_s", "agent", "x", "y"])
        rows = []
        comp_facts = []
        total_agents = len(spec["agents"])
        done_agents = 0
        t_start = time.time()
        for comp in spec["components"]:
            cid = comp["id"]
            agents = agents_by_comp.get(cid, [])
            if not agents:
                continue
            geometry = wkt.loads(comp["polygon_wkt"])
            model = jps.CollisionFreeSpeedModel(
                strength_neighbor_repulsion=params["strength_neighbor_repulsion"],
                range_neighbor_repulsion=params["range_neighbor_repulsion"],
                strength_geometry_repulsion=params["strength_geometry_repulsion"],
                range_geometry_repulsion=params["range_geometry_repulsion"],
            )
            try:
                sim = jps.Simulation(model=model, geometry=geometry, dt=dt)
            except Exception as exc:  # geometry rejected by JuPedSim
                print(f"WARN component {cid}: JuPedSim rejected geometry: {exc}")
                return 2
            routing = jps.RoutingEngine(geometry)
            stage_for_exit = {}
            journey_for_exit = {}
            target_point = {}
            for ex in comp["exits"]:
                poly = wkt.loads(ex["polygon_wkt"])
                sid = sim.add_exit_stage(poly)
                stage_for_exit[ex["id"]] = sid
                journey_for_exit[ex["id"]] = sim.add_journey(jps.JourneyDescription([sid]))
                p = poly.point_on_surface()
                target_point[ex["id"]] = (p.x, p.y)

            id_map = {}
            pending_start = []
            for a in agents:
                pos = tuple(a["position"][:2])
                best = None
                for ex_id in a["permitted_exits"]:
                    if ex_id not in stage_for_exit:
                        continue
                    wps = routing.compute_waypoints(pos, target_point[ex_id])
                    length = sum(math.dist(wps[i], wps[i + 1]) for i in range(len(wps) - 1))
                    if best is None or length < best[0]:
                        best = (length, ex_id)
                if best is None:
                    print(f"WARN agent {a['id']}: no permitted exit in component {cid}")
                    return 2
                ex_id = best[1]
                start = float(a["start_time_s"])
                jid = sim.add_agent(
                    jps.CollisionFreeSpeedModelAgentParameters(
                        position=pos,
                        time_gap=params["time_gap_s"],
                        desired_speed=float(a["desired_speed_mps"]) if start <= 0 else 0.0,
                        radius=float(a["radius_m"]),
                        journey_id=journey_for_exit[ex_id],
                        stage_id=stage_for_exit[ex_id],
                    )
                )
                id_map[jid] = (a, ex_id, best[0])
                if start > 0:
                    pending_start.append((start, jid, float(a["desired_speed_mps"])))
            pending_start.sort()
            exit_time = {}
            frame = 0
            last_progress = 0.0
            while sim.agent_count() > 0 and sim.elapsed_time() < horizon - 1e-9:
                t = sim.elapsed_time()
                while pending_start and pending_start[0][0] <= t + 1e-9:
                    _, jid, v = pending_start.pop(0)
                    if jid not in exit_time:
                        sim.agent(jid).model.desired_speed = v
                if sim.iteration_count() % every == 0:
                    for ag in sim.agents():
                        traj.writerow(
                            [
                                cid,
                                frame,
                                f"{t:.3f}",
                                id_map[ag.id][0]["id"],
                                f"{ag.position[0]:.4f}",
                                f"{ag.position[1]:.4f}",
                            ]
                        )
                    frame += 1
                sim.iterate()
                for rid in sim.removed_agents():
                    exit_time[rid] = sim.elapsed_time()
                frac = (done_agents + len(exit_time)) / max(1, total_agents)
                tfrac = sim.elapsed_time() / horizon
                prog = max(frac, tfrac) if sim.agent_count() else 1.0
                if prog - last_progress >= 0.02:
                    print(
                        f"PROGRESS {prog:.3f} component {cid}: t={sim.elapsed_time():.1f}s, {sim.agent_count()} agents inside",
                        flush=True,
                    )
                    last_progress = prog
            for jid, (a, ex_id, plen) in id_map.items():
                if jid in exit_time:
                    rows.append(
                        [
                            a["id"],
                            "evacuated",
                            ex_id,
                            f"{exit_time[jid]:.3f}",
                            a["start_time_s"],
                            f"{plen:.3f}",
                        ]
                    )
                else:
                    rows.append([a["id"], "remaining", "", "", a["start_time_s"], f"{plen:.3f}"])
            done_agents += len(agents)
            comp_facts.append(
                {
                    "component": cid,
                    "agents": len(agents),
                    "evacuated": len(exit_time),
                    "iterations": sim.iteration_count(),
                    "end_time_s": sim.elapsed_time(),
                    "frames": frame,
                }
            )
    with open(out / "agents.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["agent", "status", "exit_id", "exit_time_s", "start_time_s", "route_length_m"])
        w.writerows(rows)
    result = {
        "engine": "jupedsim",
        "engine_version": jps.__version__,
        "shapely_version": shapely.__version__,
        "python": sys.version.split()[0],
        "model": "CollisionFreeSpeedModel",
        "dt_s": dt,
        "components": comp_facts,
        "wall_time_s": round(time.time() - t_start, 3),
    }
    (out / "runner_result.json").write_text(json.dumps(result, indent=2))
    print("PROGRESS 1.0 done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
