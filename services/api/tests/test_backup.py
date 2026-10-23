"""Backup and restoration: a backup of a deployment holding real runs, reports and evidence restores into
an empty database and empty storage, and the restored deployment verifies against the manifest."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from parallel_core import testing


def _empty_database() -> str:
    """A new database with PostGIS available and no application tables, as a fresh PostGIS server
    provides (the test template holds only the extension)."""
    admin = os.environ.get(testing.ADMIN_URL_ENV, testing.DEFAULT_ADMIN_URL)
    name = f"parallel_restore_{uuid.uuid4().hex[:8]}"
    eng = create_engine(admin, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{testing.TEMPLATE}"'))
    eng.dispose()
    return make_url(admin).set(database=name).render_as_string(hide_password=False)


def test_backup_restores_into_an_empty_deployment_and_verifies(client, world, tmp_path, monkeypatch):
    from parallel_api import demo
    from parallel_core import backup
    from parallel_core.storage import get_storage

    demo.seed(only=["urban_drainage"])  # runs, artifacts, a report and source documents
    made = backup.create(tmp_path / "backup")
    assert made["objects"] > 0 and made["rows"] > 0 and made["missing_referenced_objects"] == 0
    assert backup.verify(tmp_path / "backup")["ok"]  # the source deployment matches its own backup

    source_db, source_storage = os.environ["PARALLEL_DATABASE_URL"], os.environ["PARALLEL_STORAGE_ROOT"]
    target = _empty_database()
    try:
        monkeypatch.setenv("PARALLEL_DATABASE_URL", target)
        monkeypatch.setenv("PARALLEL_STORAGE_ROOT", str(tmp_path / "restored-storage"))
        testing.reset_caches()
        result = backup.restore(tmp_path / "backup")
        assert result["ok"] and result["objects"] == made["objects"] and result["rows"] == made["rows"]
        assert result["recorded_hashes_checked"] > 0  # originals and artifacts re-hashed after restore
        with pytest.raises(backup.BackupError, match="already has tables"):
            backup.restore(tmp_path / "backup")  # never over an existing deployment

        # a changed object is detected
        key = next(get_storage().iter_keys())
        get_storage().put_bytes(key, b"tampered")
        with pytest.raises(backup.BackupError, match="hash differs"):
            backup.verify(tmp_path / "backup")
    finally:
        monkeypatch.setenv("PARALLEL_DATABASE_URL", source_db)
        monkeypatch.setenv("PARALLEL_STORAGE_ROOT", source_storage)
        testing.reset_caches()
        testing.drop_test_database(target)

    # a damaged archive is refused before anything is restored
    with open(tmp_path / "backup" / "objects.tar", "ab") as f:
        f.write(b"\0")
    with pytest.raises(backup.BackupError, match="damaged or was altered"):
        backup.verify(tmp_path / "backup")
    demo.reset()
