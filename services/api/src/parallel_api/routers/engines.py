from __future__ import annotations

from fastapi import APIRouter, Query

from parallel_contracts.engines import AVAILABILITY_LABELS, EXECUTION_MODE_LABELS, ExecutionMode
from parallel_core.access import org_role
from parallel_core.runs import check_engine
from parallel_engines.registry import all_adapters, get_adapter

from ..deps import DB, CurrentUser
from ..errors import ApiError

router = APIRouter(prefix="/engines", tags=["engines"])


def _entry(db, org_id: str | None, adapter) -> dict:
    cap = adapter.capability
    checks = []
    for mode in cap.execution_modes:
        c = check_engine(db, org_id, cap.engine_id, mode) if org_id else adapter.check_installation(mode)
        d = c.model_dump(mode="json")
        d["availability_label"] = AVAILABILITY_LABELS[c.availability]
        d["mode_label"] = EXECUTION_MODE_LABELS[mode]
        checks.append(d)
    return {
        "capability": cap.model_dump(mode="json"),
        "default_mode": adapter.default_mode().value,
        "availability": checks,
    }


@router.get("")
def list_engines(user: CurrentUser, db: DB, organisation_id: str | None = Query(None)):
    if organisation_id and org_role(db, user, organisation_id) is None:
        raise ApiError(404, "not_found", "Organisation not found")
    return [_entry(db, organisation_id, a) for a in all_adapters()]


@router.get("/{engine_id}")
def get_engine(engine_id: str, user: CurrentUser, db: DB, organisation_id: str | None = Query(None)):
    if organisation_id and org_role(db, user, organisation_id) is None:
        raise ApiError(404, "not_found", "Organisation not found")
    try:
        adapter = get_adapter(engine_id)
    except KeyError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    return _entry(db, organisation_id, adapter)


@router.post("/{engine_id}/check")
def check(
    engine_id: str,
    user: CurrentUser,
    db: DB,
    mode: ExecutionMode | None = None,
    organisation_id: str | None = Query(None),
):
    """Run the installation check now (results are not cached)."""
    adapter = get_adapter(engine_id)
    m = mode or adapter.default_mode()
    if organisation_id:
        if org_role(db, user, organisation_id) is None:
            raise ApiError(404, "not_found", "Organisation not found")
        return check_engine(db, organisation_id, engine_id, m).model_dump(mode="json")
    return adapter.check_installation(m).model_dump(mode="json")
