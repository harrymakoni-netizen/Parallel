"""Adapter registry: the single place engine ids map to adapter implementations."""

from __future__ import annotations

from functools import cache

from parallel_contracts.engines import EngineCapability

from .base import EngineAdapter
from .capabilities import ALL_CAPABILITIES


@cache
def _adapters() -> dict[str, EngineAdapter]:
    from .adapters.commercial import (
        FLAC3DAdapter,
        PCCrashAdapter,
        ThreeDECAdapter,
        VentsimAdapter,
        VirtualCrashAdapter,
    )
    from .adapters.energyplus import EnergyPlusAdapter
    from .adapters.epanet import EpanetAdapter
    from .adapters.fds import FdsAdapter
    from .adapters.hecras import HecRasAdapter
    from .adapters.isaacsim import IsaacSimAdapter
    from .adapters.jupedsim import JuPedSimAdapter
    from .adapters.openfoam import OpenFoamAdapter
    from .adapters.simpy_ops import SimPyAdapter
    from .adapters.sumo import SumoAdapter
    from .adapters.swmm import SwmmAdapter

    adapters: list[EngineAdapter] = [
        JuPedSimAdapter(),
        IsaacSimAdapter(),
        SumoAdapter(),
        FdsAdapter(),
        SwmmAdapter(),
        EpanetAdapter(),
        HecRasAdapter(),
        SimPyAdapter(),
        VentsimAdapter(),
        OpenFoamAdapter(),
        FLAC3DAdapter(),
        ThreeDECAdapter(),
        EnergyPlusAdapter(),
        VirtualCrashAdapter(),
        PCCrashAdapter(),
    ]
    out = {a.engine_id: a for a in adapters}
    missing = {c.engine_id for c in ALL_CAPABILITIES} - set(out)
    if missing:  # pragma: no cover - guarded by tests
        raise RuntimeError(f"engines without adapters: {missing}")
    return out


def get_adapter(engine_id: str) -> EngineAdapter:
    try:
        return _adapters()[engine_id]
    except KeyError as exc:
        raise KeyError(f"unknown engine '{engine_id}'") from exc


def all_adapters() -> list[EngineAdapter]:
    return list(_adapters().values())


def all_capabilities() -> list[EngineCapability]:
    return list(ALL_CAPABILITIES)
