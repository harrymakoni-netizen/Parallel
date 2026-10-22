"""Consistent error responses: {"error": {"code", "message", "details"}}."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from parallel_contracts.runs import IllegalTransition
from parallel_core.access import AccessDenied
from parallel_core.revisions import RevisionError
from parallel_core.runs import SubmissionError
from parallel_core.storage import StorageError
from parallel_engines.base import EngineError


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError):
        return JSONResponse(body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(AccessDenied)
    async def _denied(_: Request, exc: AccessDenied):
        return JSONResponse(
            body("not_found" if exc.status == 404 else "forbidden", str(exc)), status_code=exc.status
        )

    @app.exception_handler(SubmissionError)
    async def _submit(_: Request, exc: SubmissionError):
        return JSONResponse(body("submission_rejected", str(exc), exc.details), status_code=exc.status)

    @app.exception_handler(RevisionError)
    async def _rev(_: Request, exc: RevisionError):
        return JSONResponse(body("revision_immutable", str(exc)), status_code=409)

    @app.exception_handler(IllegalTransition)
    async def _trans(_: Request, exc: IllegalTransition):
        return JSONResponse(body("illegal_transition", str(exc)), status_code=409)

    @app.exception_handler(StorageError)
    async def _storage(_: Request, exc: StorageError):
        return JSONResponse(body("storage_error", str(exc)), status_code=400)

    @app.exception_handler(EngineError)
    async def _engine(_: Request, exc: EngineError):
        return JSONResponse(body(exc.code, str(exc)), status_code=422 if exc.deterministic else 502)

    @app.exception_handler(ValidationError)
    async def _pyd(_: Request, exc: ValidationError):
        return JSONResponse(
            body("invalid_content", "Content failed schema validation", exc.errors(include_url=False)),
            status_code=422,
        )

    @app.exception_handler(RequestValidationError)
    async def _req(_: Request, exc: RequestValidationError):
        return JSONResponse(
            body("invalid_request", "Request failed validation", jsonable(exc.errors())), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(body("http_error", str(exc.detail)), status_code=exc.status_code)


def jsonable(errors: Any) -> Any:
    import json

    return json.loads(json.dumps(errors, default=str))
