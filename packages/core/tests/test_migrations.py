"""The Alembic migrations produce exactly the ORM schema, and downgrade cleanly."""

from __future__ import annotations

import os

from alembic import command
from alembic.config import Config

from parallel_core import testing
from parallel_core.settings import REPO_ROOT


def test_migrations_match_models_and_round_trip():
    url = testing.create_test_database()
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "packages" / "core" / "migrations"))
    os.environ["PARALLEL_DATABASE_URL_OVERRIDE"] = url
    try:
        command.check(cfg)  # raises if the models and the migrated schema differ
        command.downgrade(cfg, "0001")
        command.upgrade(cfg, "head")
        command.check(cfg)
    finally:
        os.environ.pop("PARALLEL_DATABASE_URL_OVERRIDE", None)
        testing.drop_test_database(url)
