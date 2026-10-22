"""Isaac Sim runner — executed with Isaac Sim's own standalone Python (python.sh) on a GPU worker.

Usage: <isaac-sim>/python.sh isaacsim_runner.py <input_dir> <output_dir>

Builds a physics scene from runner_input.json (ground plane, dynamic boxes and
spheres with mass/friction/restitution), steps it headlessly and records body
states. Supports Isaac Sim 4.5+ (isaacsim.core.api) and earlier
(omni.isaac.core) module layouts. NOT verified in the build environment (no
GPU); see docs/engine-matrix.md.
"""

import csv
import json
import sys
from pathlib import Path


def main(inp: Path, out: Path) -> int:
    spec = json.loads((inp / "runner_input.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    from isaacsim import SimulationApp  # noqa: PLC0415  (must precede other omni imports)

    app = SimulationApp({"headless": True})
    try:
        import numpy as np

        try:
            from isaacsim.core.api import World
            from isaacsim.core.api.materials import PhysicsMaterial
            from isaacsim.core.api.objects import DynamicCuboid, DynamicSphere
        except ImportError:  # Isaac Sim < 4.5
            from omni.isaac.core import World
            from omni.isaac.core.materials import PhysicsMaterial
            from omni.isaac.core.objects import DynamicCuboid, DynamicSphere
        dt = float(spec["physics_dt_s"])
        world = World(stage_units_in_meters=float(spec["meters_per_unit"]), physics_dt=dt, rendering_dt=dt)
        if spec["ground_plane"]:
            world.scene.add_default_ground_plane()
        bodies = {}
        for b in spec["bodies"]:
            mat = PhysicsMaterial(
                prim_path=f"/World/Materials/{b['id']}",
                static_friction=b["static_friction"],
                dynamic_friction=b["dynamic_friction"],
                restitution=b["restitution"],
            )
            kw = dict(
                prim_path=f"/World/{b['id']}",
                name=b["id"],
                position=np.array(b["position"]),
                orientation=np.array(b["orientation_wxyz"]),
                mass=b["mass_kg"],
                physics_material=mat,
            )
            if b["shape"] == "box":
                obj = DynamicCuboid(scale=np.array(b["size"]), size=1.0, **kw)
            else:
                obj = DynamicSphere(radius=b["size"][0], **kw)
            bodies[b["id"]] = world.scene.add(obj)
        world.reset()
        for b in spec["bodies"]:
            bodies[b["id"]].set_linear_velocity(np.array(b["linear_velocity"]))
        steps = int(round(float(spec["duration_s"]) / dt))
        with open(out / "states.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "body", "x", "y", "z", "qw", "qx", "qy", "qz", "vx", "vy", "vz"])
            for i in range(steps + 1):
                t = i * dt
                for bid, obj in bodies.items():
                    p, q = obj.get_world_pose()
                    v = obj.get_linear_velocity()
                    w.writerow([f"{t:.5f}", bid, *map(float, p), *map(float, q), *map(float, v)])
                if i % max(1, steps // 50) == 0:
                    print(f"PROGRESS {i / max(steps, 1):.3f} t={t:.2f}s", flush=True)
                if i < steps:
                    world.step(render=False)
        import isaacsim

        version = getattr(isaacsim, "__version__", "unknown")
        (out / "runner_result.json").write_text(
            json.dumps(
                {"engine": "isaacsim", "engine_version": version, "steps": steps, "physics_dt_s": dt},
                indent=2,
            )
        )
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
