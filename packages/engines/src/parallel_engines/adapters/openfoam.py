"""OpenFOAM adapter — approved template ``wind2d_obstacles`` only.

The template is a steady, incompressible, plan-view 2D flow (simpleFoam with
k-epsilon and wall functions) around rectangular obstacles. The mesh is a
conformal multi-block blockMesh built on the tensor grid of domain and
obstacle edges; obstacle blocks are omitted and their faces become walls.
Mesh quality is checked with checkMesh before solving; boundary conditions are
generated from validated inputs only (no user-supplied dictionaries).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.cfd import CfdControls, CfdObstacle, CfdStudyModel
from parallel_contracts.engines import Availability, ExecutionMode, InstallationCheck, ResourceEstimate
from parallel_contracts.metrics import MetricStatus, MetricValue
from parallel_contracts.runs import ValidationIssue, ValidationReport
from parallel_contracts.scenario import ScenarioContent

from ..base import (
    EngineAdapter,
    EngineError,
    ExecutionResult,
    NormalisedFile,
    NormalisedResult,
    PreparedInputs,
    RunContext,
    RunMonitor,
    crosscheck,
    hash_inputs,
    issue,
    now,
    write_json,
)
from ..capabilities import OPENFOAM
from ..runtime import ProcessLimits, run_process

CMU = 0.09


def foam_env() -> dict[str, str]:
    root = os.environ.get("PARALLEL_OPENFOAM_DIR", "/usr/share/openfoam")
    return {"WM_PROJECT_DIR": root, "FOAM_ETC": f"{root}/etc", "WM_PROJECT_VERSION": "v1912", "HOME": "/tmp"}


def _bin(name: str) -> str | None:
    return shutil.which(name)


HEADER = "FoamFile {{ version 2.0; format ascii; class {cls}; object {obj}; }}\n"


def _grid(
    m: CfdStudyModel, obstacles: list[CfdObstacle]
) -> tuple[list[float], list[float], list[int], list[int]]:
    xs = sorted({m.domain_x0, m.domain_x1, *[o.x0 for o in obstacles], *[o.x1 for o in obstacles]})
    ys = sorted({m.domain_y0, m.domain_y1, *[o.y0 for o in obstacles], *[o.y1 for o in obstacles]})
    nx = [max(1, math.ceil((b - a) / m.target_cell_size_m - 1e-9)) for a, b in zip(xs, xs[1:], strict=False)]
    ny = [max(1, math.ceil((b - a) / m.target_cell_size_m - 1e-9)) for a, b in zip(ys, ys[1:], strict=False)]
    return xs, ys, nx, ny


def _solid(i: int, j: int, xs, ys, obstacles: list[CfdObstacle]) -> bool:
    cx, cy = (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2
    return any(o.x0 < cx < o.x1 and o.y0 < cy < o.y1 for o in obstacles)


def block_mesh_dict(m: CfdStudyModel, obstacles: list[CfdObstacle]) -> tuple[str, int]:
    xs, ys, nx, ny = _grid(m, obstacles)
    NX, NY = len(xs), len(ys)
    dz = m.target_cell_size_m

    def v(i: int, j: int, k: int) -> int:
        return k * NX * NY + j * NX + i

    verts = [f"({x:g} {y:g} {z:g})" for z in (0.0, dz) for y in ys for x in xs]
    blocks, inlet, outlet, sides, walls, fb = [], [], [], [], [], []
    cells = 0
    for j in range(NY - 1):
        for i in range(NX - 1):
            if _solid(i, j, xs, ys, obstacles):
                continue
            hexv = [
                v(i, j, 0),
                v(i + 1, j, 0),
                v(i + 1, j + 1, 0),
                v(i, j + 1, 0),
                v(i, j, 1),
                v(i + 1, j, 1),
                v(i + 1, j + 1, 1),
                v(i, j + 1, 1),
            ]
            blocks.append(f"hex ({' '.join(map(str, hexv))}) ({nx[i]} {ny[j]} 1) simpleGrading (1 1 1)")
            cells += nx[i] * ny[j]
            west = (v(i, j, 0), v(i, j, 1), v(i, j + 1, 1), v(i, j + 1, 0))
            east = (v(i + 1, j, 0), v(i + 1, j + 1, 0), v(i + 1, j + 1, 1), v(i + 1, j, 1))
            south = (v(i, j, 0), v(i + 1, j, 0), v(i + 1, j, 1), v(i, j, 1))
            north = (v(i, j + 1, 0), v(i, j + 1, 1), v(i + 1, j + 1, 1), v(i + 1, j + 1, 0))
            if i == 0:
                inlet.append(west)
            elif _solid(i - 1, j, xs, ys, obstacles):
                walls.append(west)
            if i == NX - 2:
                outlet.append(east)
            elif _solid(i + 1, j, xs, ys, obstacles):
                walls.append(east)
            if j == 0:
                sides.append(south)
            elif _solid(i, j - 1, xs, ys, obstacles):
                walls.append(south)
            if j == NY - 2:
                sides.append(north)
            elif _solid(i, j + 1, xs, ys, obstacles):
                walls.append(north)
            fb.append((hexv[0], hexv[3], hexv[2], hexv[1]))
            fb.append((hexv[4], hexv[5], hexv[6], hexv[7]))

    def faces(fs) -> str:
        return " ".join("(" + " ".join(map(str, f)) + ")" for f in fs)

    patches = [
        ("inlet", "patch", inlet),
        ("outlet", "patch", outlet),
        ("sides", "symmetry", sides),
        ("obstacles", "wall", walls),
        ("frontAndBack", "empty", fb),
    ]
    boundary = "\n".join(f"  {n} {{ type {t}; faces ( {faces(fs)} ); }}" for n, t, fs in patches if fs)
    text = (
        HEADER.format(cls="dictionary", obj="blockMeshDict") + "scale 1;\n"
        f"vertices (\n  {chr(10).join('  ' + s for s in verts)}\n);\n"
        f"blocks (\n  {chr(10).join('  ' + b for b in blocks)}\n);\nedges ();\n"
        f"boundary (\n{boundary}\n);\nmergePatchPairs ();\n"
    )
    return text, cells


def _case_files(
    m: CfdStudyModel, c: CfdControls, obstacles: list[CfdObstacle], speed: float
) -> dict[str, str]:
    k = 1.5 * (m.turbulence_intensity * speed) ** 2
    eps = CMU**0.75 * k**1.5 / m.turbulent_length_scale_m
    has_walls = bool(obstacles)
    wall = lambda t, extra="": f"  obstacles {{ type {t};{extra} }}\n" if has_walls else ""  # noqa: E731
    common_tail = "  sides { type symmetry; }\n  frontAndBack { type empty; }\n"
    files = {
        "system/controlDict": HEADER.format(cls="dictionary", obj="controlDict")
        + (
            f"application simpleFoam; startFrom startTime; startTime 0; stopAt endTime; endTime {c.iterations};\n"
            f"deltaT 1; writeControl timeStep; writeInterval {c.iterations}; purgeWrite 0; writeFormat ascii;\n"
            "writePrecision 8; writeCompression off; timeFormat general; timePrecision 6; runTimeModifiable false;\n"
        ),
        "system/fvSchemes": HEADER.format(cls="dictionary", obj="fvSchemes")
        + (
            "ddtSchemes { default steadyState; }\ngradSchemes { default Gauss linear; }\n"
            "divSchemes { default none; div(phi,U) bounded Gauss linearUpwind grad(U); div(phi,k) bounded Gauss upwind;\n"
            "  div(phi,epsilon) bounded Gauss upwind; div((nuEff*dev2(T(grad(U))))) Gauss linear; }\n"
            "laplacianSchemes { default Gauss linear corrected; }\ninterpolationSchemes { default linear; }\n"
            "snGradSchemes { default corrected; }\nwallDist { method meshWave; }\n"
        ),
        "system/fvSolution": HEADER.format(cls="dictionary", obj="fvSolution")
        + (
            "solvers {\n  p { solver GAMG; smoother GaussSeidel; tolerance 1e-7; relTol 0.05; }\n"
            '  "(U|k|epsilon)" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-7; relTol 0.1; }\n}\n'
            "SIMPLE { nNonOrthogonalCorrectors 0; consistent yes;\n"
            '  residualControl { p 1e-4; U 1e-5; "(k|epsilon)" 1e-5; } }\n'
            'relaxationFactors { equations { U 0.9; ".*" 0.9; } }\n'
        ),
        "constant/transportProperties": HEADER.format(cls="dictionary", obj="transportProperties")
        + (f"transportModel Newtonian;\nnu {m.kinematic_viscosity_m2_s:g};\n"),
        "constant/turbulenceProperties": HEADER.format(cls="dictionary", obj="turbulenceProperties")
        + ("simulationType RAS;\nRAS { RASModel kEpsilon; turbulence on; printCoeffs on; }\n"),
        "0/U": HEADER.format(cls="volVectorField", obj="U")
        + (
            f"dimensions [0 1 -1 0 0 0 0];\ninternalField uniform ({speed:g} 0 0);\nboundaryField {{\n"
            f"  inlet {{ type fixedValue; value uniform ({speed:g} 0 0); }}\n"
            "  outlet { type inletOutlet; inletValue uniform (0 0 0); value uniform (0 0 0); }\n"
            + wall("noSlip")
            + common_tail
            + "}\n"
        ),
        "0/p": HEADER.format(cls="volScalarField", obj="p")
        + (
            "dimensions [0 2 -2 0 0 0 0];\ninternalField uniform 0;\nboundaryField {\n"
            "  inlet { type zeroGradient; }\n  outlet { type fixedValue; value uniform 0; }\n"
            + wall("zeroGradient")
            + common_tail
            + "}\n"
        ),
        "0/k": HEADER.format(cls="volScalarField", obj="k")
        + (
            f"dimensions [0 2 -2 0 0 0 0];\ninternalField uniform {k:g};\nboundaryField {{\n"
            f"  inlet {{ type fixedValue; value uniform {k:g}; }}\n"
            f"  outlet {{ type inletOutlet; inletValue uniform {k:g}; value uniform {k:g}; }}\n"
            + wall("kqRWallFunction", f" value uniform {k:g};")
            + common_tail
            + "}\n"
        ),
        "0/epsilon": HEADER.format(cls="volScalarField", obj="epsilon")
        + (
            f"dimensions [0 2 -3 0 0 0 0];\ninternalField uniform {eps:g};\nboundaryField {{\n"
            f"  inlet {{ type fixedValue; value uniform {eps:g}; }}\n"
            f"  outlet {{ type inletOutlet; inletValue uniform {eps:g}; value uniform {eps:g}; }}\n"
            + wall("epsilonWallFunction", f" value uniform {eps:g};")
            + common_tail
            + "}\n"
        ),
        "0/nut": HEADER.format(cls="volScalarField", obj="nut")
        + (
            "dimensions [0 2 -1 0 0 0 0];\ninternalField uniform 0;\nboundaryField {\n"
            "  inlet { type calculated; value uniform 0; }\n  outlet { type calculated; value uniform 0; }\n"
            + wall("nutkWallFunction", " value uniform 0;")
            + common_tail
            + "}\n"
        ),
    }
    return files


def _effective_obstacles(m: CfdStudyModel, c: CfdControls) -> list[CfdObstacle]:
    return [o for o in m.obstacles if o.id not in c.removed_obstacles] + list(c.added_obstacles)


class OpenFoamAdapter(EngineAdapter):
    capability = OPENFOAM

    def check_installation(self, mode: ExecutionMode | None = None) -> InstallationCheck:
        mode = mode or ExecutionMode.managed
        missing = [b for b in ("blockMesh", "checkMesh", "simpleFoam") if _bin(b) is None]
        if missing:
            return self._check(
                mode,
                Availability.installation_required,
                details=[f"missing executables: {missing}"],
                remedy="Install OpenFOAM v1912 (Ubuntu 24.04: apt install openfoam) on the worker.",
            )
        etc = Path(foam_env()["FOAM_ETC"])
        if not (etc / "controlDict").exists():
            return self._check(
                mode,
                Availability.configuration_required,
                details=[f"{etc}/controlDict not found"],
                remedy="Set PARALLEL_OPENFOAM_DIR to the OpenFOAM installation (contains etc/).",
            )
        bashrc = (etc / "bashrc").read_text(errors="replace") if (etc / "bashrc").exists() else ""
        mver = re.search(r"WM_PROJECT_VERSION=(\S+)", bashrc)
        version = mver.group(1) if mver else None
        if version != self.capability.pinned_version:
            return self._check(
                mode,
                Availability.unsupported_version,
                version=version,
                details=[f"pinned {self.capability.pinned_version}"],
            )
        return self._check(
            mode,
            Availability.ready,
            version=version,
            details=[f"WM_PROJECT_DIR={foam_env()['WM_PROJECT_DIR']}"],
        )

    def validate_model(self, model: DomainModelEnvelope) -> ValidationReport:
        m = model.content
        if not isinstance(m, CfdStudyModel):
            return ValidationReport.from_issues(
                [issue("error", "wrong_model", "OpenFOAM requires a cfd_wind2d model")]
            )
        return ValidationReport.from_issues(self._mesh_issues(m, m.obstacles))

    def _mesh_issues(self, m: CfdStudyModel, obstacles: list[CfdObstacle]) -> list[ValidationIssue]:
        issues = []
        for a in obstacles:
            for b in obstacles:
                if a.id < b.id and a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1:
                    issues.append(
                        issue(
                            "error",
                            "overlapping_obstacles",
                            f"Obstacles {a.id} and {b.id} overlap",
                            hint="Merge them into one obstacle.",
                        )
                    )
            if a.x1 - a.x0 < m.target_cell_size_m or a.y1 - a.y0 < m.target_cell_size_m:
                issues.append(issue("warning", "under_resolved", f"Obstacle {a.id} is smaller than one cell"))
        _, cells = block_mesh_dict(m, obstacles)
        if cells > m.max_cells:
            issues.append(
                issue(
                    "error",
                    "mesh_too_large",
                    f"Mesh would have {cells} cells (limit {m.max_cells})",
                    hint="Increase target_cell_size_m.",
                )
            )
        re_ = m.inlet_speed_mps * (m.domain_y1 - m.domain_y0) / m.kinematic_viscosity_m2_s
        issues.append(
            issue(
                "info", "template", f"Template wind2d_obstacles: {cells} cells, Re≈{re_:.2e}, k-epsilon RANS."
            )
        )
        return issues

    def _validate_controls(
        self, model: DomainModelEnvelope, scenario: ScenarioContent
    ) -> list[ValidationIssue]:
        m, c = model.content, scenario.controls
        if not isinstance(m, CfdStudyModel) or not isinstance(c, CfdControls):
            return [issue("error", "wrong_controls", "CFD scenario controls required")]
        ids = {o.id for o in m.obstacles}
        out = [
            issue("error", "unknown_obstacle", f"Unknown obstacle {o}")
            for o in c.removed_obstacles
            if o not in ids
        ]
        try:
            m2 = CfdStudyModel.model_validate(
                {**m.model_dump(), "obstacles": [o.model_dump() for o in _effective_obstacles(m, c)]}
            )
            out += [i for i in self._mesh_issues(m2, m2.obstacles) if i.severity == "error"]
        except ValueError as exc:
            out.append(issue("error", "invalid_obstacles", str(exc)))
        return out

    def estimate_resources(self, model: DomainModelEnvelope, scenario: ScenarioContent) -> ResourceEstimate:
        m, c = model.content, scenario.controls
        assert isinstance(m, CfdStudyModel) and isinstance(c, CfdControls)
        _, cells = block_mesh_dict(m, _effective_obstacles(m, c))
        est = 5 + cells * c.iterations * 4e-6
        return ResourceEstimate(
            cpu_cores=1,
            memory_mb=1024,
            expected_runtime_s=round(est, 1),
            basis=f"{cells} cells x {c.iterations} iterations x 4 µs (reference workflow, upper bound)",
            within_limits=cells <= m.max_cells,
        )

    def prepare_inputs(self, ctx: RunContext) -> PreparedInputs:
        m, c = ctx.model.content, ctx.scenario.controls
        assert isinstance(m, CfdStudyModel) and isinstance(c, CfdControls)
        obstacles = _effective_obstacles(m, c)
        speed = c.inlet_speed_mps or m.inlet_speed_mps
        notes = [f"Obstacle {o} removed." for o in c.removed_obstacles]
        notes += [
            f"Obstacle {o.id} ({o.name}) added: x {o.x0:g}-{o.x1:g} m, y {o.y0:g}-{o.y1:g} m."
            for o in c.added_obstacles
        ]
        if c.inlet_speed_mps:
            notes.append(f"Inlet speed set to {speed:g} m/s.")
        case = ctx.workspace.input_dir / "case"
        bm, cells = block_mesh_dict(m, obstacles)
        files: dict[str, Path] = {}
        for rel, text in {"system/blockMeshDict": bm, **_case_files(m, c, obstacles, speed)}.items():
            p = case / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            files[f"case/{rel}"] = p
        files["study.json"] = write_json(
            ctx.workspace.input_dir / "study.json",
            {
                "template": m.template,
                "inlet_speed_mps": speed,
                "cells": cells,
                "threshold_mps": c.comfort_threshold_mps,
                "iterations": c.iterations,
                "obstacles": [o.model_dump() for o in obstacles],
                "cell_depth_m": m.target_cell_size_m,
                "domain": [m.domain_x0, m.domain_x1, m.domain_y0, m.domain_y1],
            },
        )
        return PreparedInputs(
            files=files,
            hashes=hash_inputs(files),
            model_change_notes=notes,
            summary={
                "template": m.template,
                "cells": cells,
                "inlet_speed_mps": speed,
                "iterations": c.iterations,
            },
        )

    def execute(self, ctx: RunContext, prepared: PreparedInputs, monitor: RunMonitor) -> ExecutionResult:
        started = now()
        src = ctx.workspace.input_dir / "case"
        case = ctx.workspace.output_dir / "case"
        if case.exists():
            shutil.rmtree(case)
        shutil.copytree(src, case)
        env = foam_env()
        timeout = float(ctx.scenario.settings.timeout_s)
        limits = ProcessLimits(timeout_s=timeout, address_space_mb=None, cores=1)
        logs = {}
        steps = [
            ("blockMesh", ["blockMesh"], 0.0, 0.05),
            ("checkMesh", ["checkMesh"], 0.05, 0.05),
            ("simpleFoam", ["simpleFoam"], 0.10, 0.85),
        ]
        for name, argv, base, span in steps:
            log = ctx.workspace.output_dir / f"log.{name}"
            logs[name] = log
            monitor.heartbeat(base, f"running {name}")
            out = run_process(
                argv,
                cwd=case,
                log_path=log,
                monitor=monitor,
                limits=limits,
                env_extra=env,
                progress_base=base,
                progress_span=span,
                label=name,
            )
            text = log.read_text(errors="replace")
            if out.exit_code != 0 or "FOAM FATAL" in text:
                raise EngineError(
                    f"{name} failed:\n" + "\n".join(text.splitlines()[-20:]),
                    deterministic=name in ("blockMesh", "checkMesh"),
                    code="model_error",
                )
            if name == "checkMesh" and "Mesh OK." not in text:
                raise EngineError(
                    "checkMesh reported mesh quality failures:\n" + "\n".join(text.splitlines()[-30:]),
                    deterministic=True,
                    code="mesh_check_failed",
                )
        ver = re.search(r"Version:\s*(v\d+)", logs["simpleFoam"].read_text(errors="replace"))
        return ExecutionResult(
            engine_version=ver.group(1) if ver else "v1912",
            runtime={"openfoam_dir": env["WM_PROJECT_DIR"], "logs": [str(p.name) for p in logs.values()]},
            exit_code=0,
            stdout_log=logs["simpleFoam"],
            started_at=started,
            finished_at=now(),
        )

    def normalise_results(
        self, ctx: RunContext, prepared: PreparedInputs, execution: ExecutionResult
    ) -> NormalisedResult:
        case = ctx.workspace.output_dir / "case"
        study = json.loads(prepared.files["study.json"].read_text())
        times = sorted(
            (p for p in case.iterdir() if p.is_dir() and _is_number(p.name) and p.name != "0"),
            key=lambda p: float(p.name),
        )
        if not times:
            raise EngineError("simpleFoam wrote no solution time directory")
        last = times[-1]
        U = _read_vector_field(last / "U")
        C, area = cell_geometry(case / "constant" / "polyMesh")
        if len(C) != len(U):
            raise EngineError(f"mesh has {len(C)} cells but U has {len(U)} values")
        speed = np.linalg.norm(U[:, :2], axis=1)
        thr = study["threshold_mps"]
        log = (ctx.workspace.output_dir / "log.simpleFoam").read_text(errors="replace")
        res = re.findall(r"Solving for p, Initial residual = ([0-9.eE+-]+)", log)
        conv = re.search(r"SIMPLE solution converged in (\S+) iterations", log)
        cm = (ctx.workspace.output_dir / "log.checkMesh").read_text(errors="replace")
        ncells = re.search(r"cells:\s+(\d+)", cm)
        metrics = [
            MetricValue(metric_id="cfd.max_speed", value=float(speed.max()), unit="m/s"),
            MetricValue(
                metric_id="cfd.exceedance_area",
                value=float(area[speed > thr].sum()),
                unit="m2",
                note=f"threshold {thr:g} m/s",
            ),
            MetricValue(
                metric_id="cfd.final_residual_p",
                value=float(res[-1]) if res else None,
                unit="1",
                status=MetricStatus.available if res else MetricStatus.unavailable,
            ),
            MetricValue(
                metric_id="cfd.mesh_cells",
                value=float(ncells.group(1)) if ncells else float(len(U)),
                unit="count",
            ),
        ]
        dom = (study["domain"][1] - study["domain"][0]) * (study["domain"][3] - study["domain"][2])
        solid = sum((o["x1"] - o["x0"]) * (o["y1"] - o["y0"]) for o in study["obstacles"])
        crosschecks = [
            crosscheck(
                "total cell area vs domain minus obstacles",
                float(area.sum()),
                dom - solid,
                1e-6,
                1e-9,
                "Axis-aligned hex cells tile the fluid region exactly.",
            ),
            crosscheck(
                "cell count: field vs checkMesh",
                float(len(U)),
                float(ncells.group(1)) if ncells else None,
                0,
                0,
                "The solved field must cover every mesh cell.",
            ),
            crosscheck(
                "cell count: field vs template",
                float(len(U)),
                float(study["cells"]),
                0,
                0,
                "blockMesh must produce exactly the cells defined by the template grid.",
            ),
        ]
        warnings = []
        if conv is None:
            warnings.append(
                f"SIMPLE did not reach the residual targets within {study['iterations']} iterations "
                f"(final p residual {res[-1] if res else 'n/a'}); treat the field as not converged."
            )
        norm = ctx.workspace.normalised_dir
        tbl = pa.table(
            {"x": C[:, 0], "y": C[:, 1], "ux": U[:, 0], "uy": U[:, 1], "speed": speed, "area_m2": area}
        )
        pq.write_table(tbl, norm / "field.parquet")
        field = {
            "x": np.round(C[:, 0], 3).tolist(),
            "y": np.round(C[:, 1], 3).tolist(),
            "speed": np.round(speed, 3).tolist(),
            "cell_size_m": study["cell_depth_m"],
            "obstacles": study["obstacles"],
            "threshold_mps": thr,
            "note": "Plan-view 2D steady RANS field from the approved template; not a comfort or safety assessment.",
        }
        (norm / "field.json").write_text(json.dumps(field, separators=(",", ":")))
        return NormalisedResult(
            metrics=metrics,
            crosschecks=crosschecks,
            warnings=warnings,
            files=[
                NormalisedFile(
                    "field.parquet",
                    norm / "field.parquet",
                    "field",
                    "application/vnd.apache.parquet",
                    "Cell-centre velocity field (m/s) and cell areas (m²)",
                ),
                NormalisedFile(
                    "field.json",
                    norm / "field.json",
                    "field",
                    "application/json",
                    "Browser derivative of the speed field",
                ),
            ],
            summary={
                **prepared.summary,
                "converged": conv is not None,
                "converged_iterations": int(conv.group(1)) if conv else None,
                "final_time": last.name,
            },
            units={"speed": "m/s", "area": "m2"},
        )


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _internal_field_block(path: Path) -> tuple[str, int, str]:
    text = path.read_text(errors="replace")
    m = re.search(r"internalField\s+nonuniform\s+List<(\w+)>\s*(\d+)\s*\(", text)
    if not m:
        raise EngineError(f"cannot read nonuniform internalField from {path.name}")
    n = int(m.group(2))
    start = m.end()
    end = text.index("\n)", start)
    return text[start:end], n, m.group(1)


def _read_vector_field(path: Path) -> np.ndarray:
    body, n, kind = _internal_field_block(path)
    vals = np.array(re.findall(r"\(([^()]+)\)", body), dtype=object)
    arr = np.array([[float(x) for x in v.split()] for v in vals])
    if arr.shape != (n, 3):
        raise EngineError(f"{path.name}: expected {n} vectors, parsed {arr.shape}")
    return arr


def _foam_list(path: Path) -> str:
    """Return the body of the top-level list in an ASCII polyMesh file (after the FoamFile header)."""
    text = path.read_text(errors="replace")
    hdr_end = text.index("}", text.index("FoamFile")) + 1
    m = re.search(r"\n(\d+)\s*\n\(", text[hdr_end:])
    if not m:
        raise EngineError(f"cannot parse list in {path.name}")
    start = hdr_end + m.end()
    return text[start : text.rindex(")")]


def cell_geometry(poly: Path) -> tuple[np.ndarray, np.ndarray]:
    """Cell centres (x, y) and plan areas for the template's axis-aligned hex cells.

    Computed from points/faces/owner/neighbour: each cell's vertex set is the
    union of its faces' vertices; centre = mean of the 8 vertices, area = x-extent
    times y-extent (exact for axis-aligned hexahedra).
    """
    pts = np.array(
        [[float(v) for v in p.split()] for p in re.findall(r"\(([^()]+)\)", _foam_list(poly / "points"))]
    )
    faces = [[int(i) for i in f.split()] for f in re.findall(r"\d+\(([^()]+)\)", _foam_list(poly / "faces"))]
    owner = np.array(_foam_list(poly / "owner").split(), dtype=int)
    neigh = np.array(_foam_list(poly / "neighbour").split(), dtype=int)
    n_cells = int(owner.max()) + 1
    verts: list[set[int]] = [set() for _ in range(n_cells)]
    for fi, f in enumerate(faces):
        verts[owner[fi]].update(f)
        if fi < len(neigh):
            verts[neigh[fi]].update(f)
    centres = np.zeros((n_cells, 2))
    areas = np.zeros(n_cells)
    for c, vs in enumerate(verts):
        p = pts[sorted(vs)]
        if len(p) != 8:
            raise EngineError(f"cell {c} is not a hexahedron ({len(p)} vertices)")
        centres[c] = p[:, :2].mean(axis=0)
        areas[c] = (p[:, 0].max() - p[:, 0].min()) * (p[:, 1].max() - p[:, 1].min())
    return centres, areas
