"""File-exchange packages (spec section 15.3).

A file exchange is a documented operator workflow, labelled as such: Parallel
builds a hashed input package (README + manifest + data), the run waits in
``awaiting_external_result``, and the operator uploads a result package that is
validated, hashed and normalised with full provenance (who imported it, which
external installation produced it). It is never presented as automated
execution.

Archive handling treats uploads as untrusted: member names are checked for
absolute paths and traversal, symlinks are refused, and member/total sizes and
counts are bounded.
"""

from __future__ import annotations

import json
import stat
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .base import EngineError, sha256_file

EXCHANGE_VERSION = "1.0"


@dataclass
class ArchiveLimits:
    max_members: int = 2000
    max_member_bytes: int = 2 * 1024**3
    max_total_bytes: int = 8 * 1024**3
    max_ratio: float = 200.0  # compression ratio guard against zip bombs


class UnsafeArchive(EngineError):
    def __init__(self, message: str):
        super().__init__(message, deterministic=True, code="unsafe_archive")


def build_package(
    *,
    engine_id: str,
    engine_name: str,
    run_id: str,
    files: dict[str, Path],
    readme: str,
    result_template: dict[str, Any],
    dest: Path,
    extra: dict[str, Any] | None = None,
) -> Path:
    manifest = {
        "parallel_exchange_version": EXCHANGE_VERSION,
        "engine_id": engine_id,
        "engine_name": engine_name,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "execution_mode": "file_exchange",
        "label": "File exchange: the engine is run by an operator outside Parallel; this is not automated execution.",
        "coordinate_frame": "Parallel canonical local frame: right-handed, Z-up, metres",
        "files": [
            {"path": f"data/{name}", "sha256": sha256_file(p), "size_bytes": p.stat().st_size}
            for name, p in sorted(files.items())
        ],
        "result_template": result_template,
        **(extra or {}),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", readme)
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        for name, p in sorted(files.items()):
            z.write(p, f"data/{name}")
    return dest


def safe_extract(archive: Path, dest: Path, limits: ArchiveLimits | None = None) -> list[Path]:
    limits = limits or ArchiveLimits()
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    try:
        z = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise UnsafeArchive("not a valid zip archive") from exc
    with z:
        infos = z.infolist()
        if len(infos) > limits.max_members:
            raise UnsafeArchive(f"archive has {len(infos)} members (limit {limits.max_members})")
        total = 0
        for info in infos:
            name = info.filename
            pp = PurePosixPath(name)
            if pp.is_absolute() or ".." in pp.parts or name.startswith(("/", "\\")) or ":" in pp.parts[0]:
                raise UnsafeArchive(f"unsafe member path: {name!r}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UnsafeArchive(f"symbolic links are not allowed: {name!r}")
            if info.file_size > limits.max_member_bytes:
                raise UnsafeArchive(f"member {name!r} exceeds the size limit")
            if info.compress_size and info.file_size / max(info.compress_size, 1) > limits.max_ratio:
                raise UnsafeArchive(f"member {name!r} has a suspicious compression ratio")
            total += info.file_size
            if total > limits.max_total_bytes:
                raise UnsafeArchive("archive exceeds the total size limit")
        for info in infos:
            if info.is_dir():
                continue
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest) + "/"):
                raise UnsafeArchive(f"member escapes destination: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                written = 0
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    if written > limits.max_member_bytes:
                        raise UnsafeArchive(
                            f"member {info.filename!r} exceeds the size limit when decompressed"
                        )
                    dst.write(chunk)
            out.append(target)
    return out


def readme(
    title: str, steps: list[str], files: dict[str, str], result_files: dict[str, str], notes: list[str]
) -> str:
    lines = [
        f"# {title}",
        "",
        "This is a **file exchange package** produced by Parallel. The engine is run by an operator outside Parallel;",
        "Parallel does not execute it. Results are imported back with full provenance.",
        "",
        "## Operator steps",
        "",
    ]
    lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    lines += ["", "## Package contents", ""] + [f"- `data/{k}` — {v}" for k, v in files.items()]
    lines += [
        "",
        "## Result package to upload",
        "",
        "Create a zip containing the following files (exact names), then upload it to the waiting run:",
        "",
    ]
    lines += [f"- `{k}` — {v}" for k, v in result_files.items()]
    if notes:
        lines += ["", "## Notes and limitations", ""] + [f"- {n}" for n in notes]
    lines += [
        "",
        "Units are SI (metres, seconds, kilograms) in Parallel's canonical local frame (right-handed, Z-up).",
    ]
    return "\n".join(lines) + "\n"
