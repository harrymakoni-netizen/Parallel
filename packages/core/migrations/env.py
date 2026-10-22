"""Alembic environment: migrations for the Parallel schema (PostgreSQL + PostGIS)."""

from __future__ import annotations

import os

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import create_engine

from parallel_core import models  # noqa: F401  (registers tables)
from parallel_core.db import Base
from parallel_core.settings import get_settings

config = context.config
target_metadata = Base.metadata

POSTGIS_TABLES = {
    "spatial_ref_sys",
    "geography_columns",
    "geometry_columns",
    "raster_columns",
    "raster_overviews",
}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name in POSTGIS_TABLES:
        return False
    return alembic_helpers.include_object(obj, name, type_, reflected, compare_to)


def compare_type(ctx, inspected_column, metadata_column, inspected_type, metadata_type):
    # GeoAlchemy2 reflects SRID 0 geometries without the SRID; compare GIS columns by migration review instead.
    from geoalchemy2.types import _GISType

    if isinstance(metadata_type, _GISType):
        return False
    return None


def url() -> str:
    return (
        os.environ.get("PARALLEL_DATABASE_URL_OVERRIDE")
        or config.get_main_option("sqlalchemy.url")
        or get_settings().database_url
    )


def run_migrations_offline() -> None:
    context.configure(
        url=url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        process_revision_directives=alembic_helpers.writer,
        render_item=alembic_helpers.render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            process_revision_directives=alembic_helpers.writer,
            render_item=alembic_helpers.render_item,
            compare_type=compare_type,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
