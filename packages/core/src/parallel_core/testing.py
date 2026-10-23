"""Test helpers: disposable PostgreSQL databases with the real schema applied by Alembic."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from .settings import REPO_ROOT

ADMIN_URL_ENV = "PARALLEL_TEST_ADMIN_URL"
DEFAULT_ADMIN_URL = "postgresql+psycopg://parallel:parallel@localhost:5432/postgres"
TEMPLATE = os.environ.get("PARALLEL_TEST_TEMPLATE", "parallel_template")


def create_test_database() -> str:
    admin = os.environ.get(ADMIN_URL_ENV, DEFAULT_ADMIN_URL)
    name = f"parallel_test_{uuid.uuid4().hex[:10]}"
    eng = create_engine(admin, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE}"'))
    eng.dispose()
    url = make_url(admin).set(database=name).render_as_string(hide_password=False)
    migrate(url)
    return url


def migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "packages" / "core" / "migrations"))
    os.environ["PARALLEL_DATABASE_URL_OVERRIDE"] = url
    try:
        command.upgrade(cfg, "head")
    finally:
        os.environ.pop("PARALLEL_DATABASE_URL_OVERRIDE", None)


def drop_test_database(url: str) -> None:
    from .db import get_engine

    get_engine.cache_clear()
    admin = os.environ.get(ADMIN_URL_ENV, DEFAULT_ADMIN_URL)
    name = make_url(url).database
    eng = create_engine(admin, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    eng.dispose()


def configure_environment(url: str, tmp: Path, eager: bool = True) -> None:
    """Point settings, storage and workspaces at test locations and clear cached singletons."""
    os.environ["PARALLEL_DATABASE_URL"] = url
    os.environ["PARALLEL_STORAGE_ROOT"] = str(tmp / "storage")
    os.environ["PARALLEL_WORKSPACE_ROOT"] = str(tmp / "workspaces")
    os.environ["PARALLEL_TASK_ALWAYS_EAGER"] = "1" if eager else "0"
    os.environ["PARALLEL_ENVIRONMENT"] = "test"
    os.environ.setdefault("PARALLEL_SECRET_KEY", "test-secret-key-for-parallel-tests-0123456789")
    # a Redis database of its own, so a development worker on the same host never takes test tasks
    os.environ["PARALLEL_REDIS_URL"] = os.environ.get("PARALLEL_TEST_REDIS_URL", "redis://localhost:6379/15")
    reset_caches()


def reset_caches() -> None:
    from . import db, events, settings, storage

    settings.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db._factory.cache_clear()
    storage.get_storage.cache_clear()
    events._client.cache_clear()
    jobs = sys.modules.get("parallel_core.jobs")
    if jobs is not None:  # the Celery app is built at import; point it at the current broker
        url = settings.get_settings().redis_url
        jobs.celery_app.conf.update(broker_url=url, result_backend=url)
