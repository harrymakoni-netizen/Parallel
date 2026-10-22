"""Isolated engine runtimes and restricted subprocess execution.

Each Python-based engine runs in its own virtual environment under
``$PARALLEL_RUNTIMES_DIR/<engine_id>/`` (created by ``scripts/setup_runtimes.sh``
from ``packages/engines/runtimes/<engine_id>/requirements.txt``). No single
Python environment has to contain every engine.

Runner scripts are Parallel's own controlled templates; they receive data only
(JSON + native model files), never uploaded code. Processes run in their own
session with a minimal environment, CPU-time and address-space limits, a
wall-clock timeout, heartbeats and cooperative cancellation.

Runner protocol (stdout lines):
    PROGRESS <fraction 0..1> <message>
    WARN <message>
    anything else is logged.
Exit code 0 = success, 2 = deterministic model error (no retry), other = failure.
"""

from __future__ import annotations

import os
import resource
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .base import EngineError, RunCancelled, RunMonitor, RunTimedOut

REPO_ROOT = Path(__file__).resolve().parents[4]
RUNNERS_DIR = Path(__file__).resolve().parent / "runners"
RUNTIME_SPECS_DIR = Path(__file__).resolve().parents[2] / "runtimes"


def runtimes_dir() -> Path:
    return Path(os.environ.get("PARALLEL_RUNTIMES_DIR", REPO_ROOT / ".runtimes"))


def runtime_python(engine_id: str) -> Path:
    return runtimes_dir() / engine_id / "bin" / "python"


def runner_script(name: str) -> Path:
    return RUNNERS_DIR / name


def pinned_requirements(engine_id: str) -> dict[str, str]:
    req = RUNTIME_SPECS_DIR / engine_id / "requirements.txt"
    pins: dict[str, str] = {}
    if req.exists():
        for line in req.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if "==" in line:
                name, ver = line.split("==", 1)
                pins[name.strip().lower()] = ver.strip()
    return pins


def probe_python_runtime(engine_id: str, code: str, timeout: float = 60.0) -> tuple[bool, str]:
    """Run a short snippet in the engine runtime (isolated mode). Returns (ok, stdout|stderr)."""
    py = runtime_python(engine_id)
    if not py.exists():
        return False, f"runtime interpreter not found at {py}"
    try:
        cp = subprocess.run(
            [str(py), "-I", "-c", code], capture_output=True, text=True, timeout=timeout, env=_minimal_env({})
        )
    except subprocess.TimeoutExpired:
        return False, "runtime probe timed out"
    if cp.returncode != 0:
        return False, (cp.stderr or cp.stdout).strip()[-2000:]
    return True, cp.stdout.strip()


def _minimal_env(extra: dict[str, str]) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "OMP_NUM_THREADS": os.environ.get("PARALLEL_ENGINE_THREADS", "2"),
    }
    env.update(extra)
    return env


@dataclass
class ProcessLimits:
    timeout_s: float = 1800.0
    cpu_seconds: int | None = None  # defaults to timeout x cores
    address_space_mb: int | None = 8192
    max_output_mb: int = 4096
    cores: int = 2


@dataclass
class ProcessOutcome:
    exit_code: int
    duration_s: float
    log_path: Path
    warnings: list[str] = field(default_factory=list)
    last_progress: float | None = None


def _preexec(limits: ProcessLimits):  # pragma: no cover - runs in the child
    def fn() -> None:
        os.setsid()
        cpu = limits.cpu_seconds or int(limits.timeout_s * max(1, limits.cores)) + 30
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 10))
        if limits.address_space_mb:
            b = limits.address_space_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (b, b))
        fsize = limits.max_output_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return fn


def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    monitor: RunMonitor,
    limits: ProcessLimits,
    env_extra: dict[str, str] | None = None,
    heartbeat_s: float = 2.0,
    progress_base: float = 0.0,
    progress_span: float = 1.0,
    label: str = "engine",
) -> ProcessOutcome:
    """Run a controlled engine command with heartbeats, cancellation and timeouts."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    warnings: list[str] = []
    state: dict[str, float | None] = {"progress": None}
    lock = threading.Lock()
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"$ {' '.join(str(a) for a in argv)}\n")
        log.flush()
        proc = subprocess.Popen(
            [str(a) for a in argv],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            env=_minimal_env(env_extra or {}),
            preexec_fn=_preexec(limits),
        )

        def reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                stripped = line.strip()
                if stripped.startswith("PROGRESS "):
                    parts = stripped.split(" ", 2)
                    try:
                        frac = float(parts[1])
                        with lock:
                            state["progress"] = progress_base + progress_span * max(0.0, min(1.0, frac))
                            state["message"] = parts[2] if len(parts) > 2 else None  # type: ignore[assignment]
                    except ValueError:
                        pass
                elif stripped.startswith("WARN "):
                    warnings.append(stripped[5:])
                monitor.log(stripped)
            log.flush()

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        last_beat = 0.0
        try:
            while True:
                rc = proc.poll()
                elapsed = time.monotonic() - started
                if rc is not None:
                    break
                if monitor.cancel_requested():
                    _kill(proc)
                    raise RunCancelled(f"{label} cancelled by request")
                if elapsed > limits.timeout_s:
                    _kill(proc)
                    raise RunTimedOut(f"{label} exceeded the {limits.timeout_s:.0f} s timeout")
                if time.monotonic() - last_beat >= heartbeat_s:
                    with lock:
                        monitor.heartbeat(state["progress"], state.get("message"))  # type: ignore[arg-type]
                    last_beat = time.monotonic()
                time.sleep(0.2)
        finally:
            t.join(timeout=10)
        with lock:  # final heartbeat so short runs still report their last progress
            monitor.heartbeat(state["progress"], state.get("message"))  # type: ignore[arg-type]
        log.write(f"[exit code {rc} after {time.monotonic() - started:.1f} s]\n")
    if rc < 0:
        sig = -rc
        name = signal.Signals(sig).name if sig in signal.Signals._value2member_map_ else str(sig)
        raise EngineError(f"{label} terminated by signal {name} (resource limit or crash); see log")
    return ProcessOutcome(rc, time.monotonic() - started, log_path, warnings, state["progress"])


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
    except ProcessLookupError:
        pass


def run_python_runner(
    engine_id: str,
    script: str,
    *,
    cwd: Path,
    args: Sequence[str],
    log_path: Path,
    monitor: RunMonitor,
    limits: ProcessLimits,
    env_extra: dict[str, str] | None = None,
    progress_base: float = 0.0,
    progress_span: float = 1.0,
) -> ProcessOutcome:
    py = runtime_python(engine_id)
    if not py.exists():
        raise EngineError(
            f"{engine_id} runtime is not installed at {py}; run scripts/setup_runtimes.sh",
            code="installation_required",
        )
    outcome = run_process(
        [str(py), "-I", str(runner_script(script)), *args],
        cwd=cwd,
        log_path=log_path,
        monitor=monitor,
        limits=limits,
        env_extra=env_extra,
        progress_base=progress_base,
        progress_span=progress_span,
        label=engine_id,
    )
    if outcome.exit_code == 2:
        tail = log_path.read_text(errors="replace").splitlines()[-15:]
        raise EngineError(
            f"{engine_id} rejected the model:\n" + "\n".join(tail), deterministic=True, code="model_error"
        )
    if outcome.exit_code != 0:
        tail = log_path.read_text(errors="replace").splitlines()[-15:]
        raise EngineError(f"{engine_id} runner failed (exit {outcome.exit_code}):\n" + "\n".join(tail))
    return outcome


def python_version_string() -> str:
    return sys.version.split()[0]
