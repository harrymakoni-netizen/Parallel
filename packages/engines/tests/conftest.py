"""Engine test helpers.

Real engine tests *fail* when the engine runtime is missing (a skipped test is
not a pass). Set PARALLEL_ALLOW_MISSING_ENGINES=1 to turn missing runtimes into
explicit, reported skips on machines that intentionally lack an engine.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from parallel_contracts.engines import Availability
from parallel_engines.base import NullMonitor, RunContext, RunWorkspace
from parallel_engines.registry import get_adapter


def require_engine(engine_id: str) -> None:
    check = get_adapter(engine_id).check_installation()
    if check.availability != Availability.ready:
        msg = f"{engine_id} not ready: {check.availability.value} {check.details}"
        if os.environ.get("PARALLEL_ALLOW_MISSING_ENGINES") == "1":
            pytest.skip(msg)
        pytest.fail(msg + " (set PARALLEL_ALLOW_MISSING_ENGINES=1 to skip explicitly)")


def run_managed(engine_id: str, model, scenario, tmp: Path, seed: int | None = 42, asset_path=None):
    adapter = get_adapter(engine_id)
    report = adapter.validate_scenario(model, scenario)
    assert report.ok, [i.message for i in report.issues if i.severity == "error"]
    ws = RunWorkspace(tmp).create()
    ctx = RunContext(
        run_id="test-run", workspace=ws, model=model, scenario=scenario, seed=seed, asset_path=asset_path
    )
    prepared = adapter.prepare_inputs(ctx)
    monitor = NullMonitor()
    execution = adapter.execute(ctx, prepared, monitor)
    result = adapter.normalise_results(ctx, prepared, execution)
    result.check_registered()
    return prepared, execution, result, monitor


def metric(result, metric_id, entity=None):
    for m in result.metrics:
        if m.metric_id == metric_id and m.entity == entity:
            return m
    raise KeyError((metric_id, entity))


@pytest.fixture
def workdir(tmp_path):
    yield tmp_path
    shutil.rmtree(tmp_path, ignore_errors=True)
