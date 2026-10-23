"""Backups of a Parallel deployment, with restoration that proves itself.

    python -m parallel_core.backup create  --out /backups/2026-10-23
    python -m parallel_core.backup restore --from /backups/2026-10-23
    python -m parallel_core.backup verify  --from /backups/2026-10-23

A backup is a directory holding the database (``db.dump``, PostgreSQL custom format), every stored
object (``objects.tar``, keys as paths) and ``manifest.json``: the schema revision, the row count of
every table, and the size and SHA-256 of every object and of both archives.

``restore`` targets the configured deployment (PARALLEL_DATABASE_URL and the storage settings). It
refuses a database that already has tables, checks the archives against the manifest first, and
finishes by running ``verify``. ``verify`` compares the live deployment with the manifest: schema
revision, row counts and every object hash, and re-checks that each source asset and run artifact
still matches the SHA-256 recorded when it was received.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from .settings import get_settings
from .storage import get_storage

FORMAT = 1


class BackupError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _pg(url: str) -> tuple[list[str], dict[str, str]]:
    """libpq connection arguments and environment for a SQLAlchemy URL (password via PGPASSWORD,
    never on the command line)."""
    u = make_url(url)
    args = ["-h", u.host or "localhost", "-p", str(u.port or 5432), "-U", u.username or "postgres", "-d", u.database]
    env = {**os.environ}
    if u.password:
        env["PGPASSWORD"] = u.password
    return args, env


def _db_state(url: str) -> dict[str, Any]:
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            tables = sorted(t for t in inspect(c).get_table_names(schema="public") if t != "spatial_ref_sys")
            counts = {t: c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tables}
            revision = c.execute(text("SELECT version_num FROM alembic_version")).scalar() if "alembic_version" in tables else None
            extensions = sorted(c.execute(text("SELECT extname FROM pg_extension WHERE extname <> 'plpgsql'")).scalars())
            assets = c.execute(text("SELECT storage_key, sha256 FROM source_asset WHERE sha256 IS NOT NULL")).all() \
                if "source_asset" in tables else []
            artifacts = c.execute(text("SELECT storage_key, sha256 FROM run_artifact")).all() if "run_artifact" in tables else []
    finally:
        eng.dispose()
    return {"tables": counts, "revision": revision, "extensions": extensions, "recorded_hashes": [*assets, *artifacts]}


def create(out: Path) -> dict[str, Any]:
    s = get_settings()
    storage = get_storage()
    out.mkdir(parents=True, exist_ok=False)
    args, env = _pg(s.database_url)
    subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "-f", str(out / "db.dump"), *args],
                   check=True, env=env)
    state = _db_state(s.database_url)
    objects = []
    with tarfile.open(out / "objects.tar", "w") as tar, tempfile.TemporaryDirectory() as tmp:
        for key in storage.iter_keys():
            local = storage.get_to_path(key, Path(tmp) / "obj")
            objects.append({"key": key, "size": local.stat().st_size, "sha256": _sha256_file(local)})
            tar.add(local, arcname=key)
    keys = {o["key"] for o in objects}
    manifest = {
        "format": FORMAT,
        "created_at": datetime.now(UTC).isoformat(),
        "schema_revision": state["revision"],
        "extensions": state["extensions"],
        "tables": state["tables"],
        "objects": objects,
        "missing_referenced_objects": sorted({k for k, _ in state["recorded_hashes"]} - keys),
        "archives": {n: _sha256_file(out / n) for n in ("db.dump", "objects.tar")},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return {"out": str(out), "tables": len(state["tables"]), "rows": sum(state["tables"].values()),
            "objects": len(objects), "bytes": sum(o["size"] for o in objects),
            "missing_referenced_objects": len(manifest["missing_referenced_objects"])}


def _manifest(src: Path) -> dict[str, Any]:
    m = json.loads((src / "manifest.json").read_text())
    if m.get("format") != FORMAT:
        raise BackupError(f"unsupported backup format {m.get('format')!r}")
    for name, digest in m["archives"].items():
        if _sha256_file(src / name) != digest:
            raise BackupError(f"{name} does not match the manifest: the backup is damaged or was altered")
    return m


def restore(src: Path) -> dict[str, Any]:
    m = _manifest(src)
    s = get_settings()
    storage = get_storage()
    target = _db_state(s.database_url)
    if target["tables"]:
        raise BackupError("the target database already has tables; restore into an empty database")
    _ensure_extensions(s.database_url, set(m.get("extensions", [])) - set(target["extensions"]))
    if next(iter(storage.iter_keys()), None) is not None:
        raise BackupError("the target storage already holds objects; restore into empty storage")
    args, env = _pg(s.database_url)
    # extensions belong to the target server (installed by its administrator), so their entries are skipped
    toc = subprocess.run(["pg_restore", "-l", str(src / "db.dump")], check=True, env=env, capture_output=True, text=True)
    keep = restore_list(toc.stdout, *_target_provided(s.database_url))
    with tempfile.NamedTemporaryFile("w", suffix=".list") as listing:
        listing.write("\n".join(keep) + "\n")
        listing.flush()
        subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "-L", listing.name, *args,
                        str(src / "db.dump")], check=True, env=env)
    with tarfile.open(src / "objects.tar") as tar, tempfile.TemporaryDirectory() as tmp:
        wanted = {o["key"] for o in m["objects"]}
        for member in tar:
            if not member.isfile() or member.name not in wanted:  # only the manifest's regular files
                raise BackupError(f"unexpected archive entry {member.name!r}")
            f = tar.extractfile(member)
            dest = Path(tmp) / "obj"
            with open(dest, "wb") as out:
                shutil.copyfileobj(f, out)
            storage.put_file(member.name, dest)
    return verify(src)


def restore_list(toc: str, schemas: set[str], extension_tables: set[tuple[str, str]]) -> list[str]:
    """The entries of a dump's table of contents to restore. Extensions, the schemas the target already
    has (an extension's, such as PostGIS's ``tiger`` and ``topology``) and the contents of extension-owned
    tables (such as ``spatial_ref_sys``) are provided by the target server, so their entries are skipped."""
    keep = []
    for ln in toc.splitlines():
        if re.search(r"\b(EXTENSION|COMMENT - EXTENSION)\b", ln):
            continue
        m = re.search(r"\b(?:SCHEMA -|COMMENT - SCHEMA) (\S+)", ln)
        if m and m.group(1) in schemas:
            continue
        if any(f"TABLE DATA {schema} {table} " in ln for schema, table in extension_tables):
            continue
        keep.append(ln)
    return keep


def _target_provided(url: str) -> tuple[set[str], set[tuple[str, str]]]:
    """The target's existing schemas and its extension-owned tables."""
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            schemas = set(c.execute(text("SELECT nspname FROM pg_namespace")).scalars())
            tables = {tuple(r) for r in c.execute(text(
                "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_depend d ON d.objid = c.oid AND d.deptype = 'e' WHERE c.relkind = 'r'"
            ))}
            return schemas, tables
    finally:
        eng.dispose()


def _ensure_extensions(url: str, missing: set[str]) -> None:
    if not missing:
        return
    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            for ext in sorted(missing):
                try:
                    c.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))
                except Exception as e:
                    raise BackupError(
                        f"the target database needs the {ext} extension; ask its administrator to run "
                        f"CREATE EXTENSION {ext} (detail: {str(e).splitlines()[0]})"
                    ) from e
    finally:
        eng.dispose()


def verify(src: Path) -> dict[str, Any]:
    m = _manifest(src)
    s = get_settings()
    storage = get_storage()
    state = _db_state(s.database_url)
    problems: list[str] = []
    if state["revision"] != m["schema_revision"]:
        problems.append(f"schema revision {state['revision']} != {m['schema_revision']}")
    for t, n in m["tables"].items():
        if state["tables"].get(t) != n:
            problems.append(f"table {t}: {state['tables'].get(t)} rows, backup has {n}")
    with tempfile.TemporaryDirectory() as tmp:
        hashes = {}
        for o in m["objects"]:
            if not storage.exists(o["key"]):
                problems.append(f"object missing: {o['key']}")
                continue
            hashes[o["key"]] = _sha256_file(storage.get_to_path(o["key"], Path(tmp) / "obj"))
            if hashes[o["key"]] != o["sha256"]:
                problems.append(f"object hash differs: {o['key']}")
    # originals and artifacts must still match the hash recorded when they were received
    recorded = 0
    for key, digest in state["recorded_hashes"]:
        if key in hashes:
            recorded += 1
            if hashes[key] != digest:
                problems.append(f"recorded SHA-256 differs for {key}")
    result = {"ok": not problems, "tables": len(m["tables"]), "rows": sum(m["tables"].values()),
              "objects": len(m["objects"]), "recorded_hashes_checked": recorded, "problems": problems[:50]}
    if problems:
        raise BackupError(json.dumps(result, indent=1))
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m parallel_core.backup", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create").add_argument("--out", type=Path, required=True)
    for name in ("restore", "verify"):
        sub.add_parser(name).add_argument("--from", dest="src", type=Path, required=True)
    a = p.parse_args(argv)
    try:
        out = create(a.out) if a.cmd == "create" else restore(a.src) if a.cmd == "restore" else verify(a.src)
    except BackupError as e:
        print(f"backup {a.cmd} failed: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
