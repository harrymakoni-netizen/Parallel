"""Parallel application API (FastAPI)."""

from __future__ import annotations

import contextlib
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from parallel_core.db import session_scope, utcnow
from parallel_core.models import EngineCapabilityRecord
from parallel_core.settings import get_settings
from parallel_engines.registry import all_adapters

from . import errors
from .routers import (
    assets,
    assistant,
    auth,
    cases,
    comparisons,
    engines,
    models,
    optimisations,
    organisations,
    projects,
    reconstructions,
    reports,
    reviews,
    runs,
    scenarios,
    scenes,
)

API_VERSION = "1.0.0"


def sync_capabilities() -> None:
    """Persist the current capability records (EngineCapability entity) for reporting and audit."""
    with session_scope() as db:
        for a in all_adapters():
            rec = db.get(EngineCapabilityRecord, a.engine_id)
            data = a.capability.model_dump(mode="json")
            if rec is None:
                db.add(
                    EngineCapabilityRecord(
                        engine_id=a.engine_id,
                        adapter_version=a.capability.adapter_version,
                        record=data,
                        updated_at=utcnow(),
                    )
                )
            elif rec.record != data:
                rec.record, rec.adapter_version, rec.updated_at = data, a.capability.adapter_version, utcnow()


@asynccontextmanager
async def lifespan(app: FastAPI):
    with contextlib.suppress(Exception):  # database not migrated yet; the health endpoint reports it
        sync_capabilities()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Parallel API",
        version=API_VERSION,
        description="Capture, reconstruct, prepare, simulate, compare and improve. All numerical results come from "
        "recorded engine executions, traceable imports or explicitly defined calculations.",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[s.web_origin],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    errors.install(app)
    api = APIRouter(prefix="/api/v1")
    for r in (
        auth,
        organisations,
        projects,
        assets,
        reconstructions,
        scenes,
        models,
        scenarios,
        engines,
        runs,
        comparisons,
        optimisations,
        cases,
        reports,
        reviews,
        assistant,
    ):
        api.include_router(r.router)

    @api.get("/metrics", tags=["meta"])
    def metric_registry() -> list[dict]:
        """Documented metric definitions: identifier, unit, definition, calculation and missing-value rule."""
        from parallel_metrics.registry import METRICS

        return [m.model_dump(mode="json") for m in METRICS.values()]

    @api.get("/health", tags=["meta"])
    def health() -> dict:
        ok_db = True
        try:
            with session_scope() as db:
                db.execute(select(1))
        except Exception:
            ok_db = False
        return {"status": "ok" if ok_db else "degraded", "database": ok_db, "version": API_VERSION}

    app.include_router(api)
    return app


app = create_app()
