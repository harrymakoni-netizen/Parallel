"""OpenFOAM reference workflow: approved template wind2d_obstacles (blockMesh, checkMesh, simpleFoam)."""

import pytest

from parallel_engines.adapters.openfoam import block_mesh_dict
from parallel_engines.reference_models import cfd_scenario, site_wind_model

from .conftest import metric, require_engine, run_managed


@pytest.fixture(autouse=True)
def _engine():
    require_engine("openfoam")


def test_template_runs_and_geometry_crosschecks_pass(workdir):
    prepared, execution, result, _ = run_managed(
        "openfoam", site_wind_model(), cfd_scenario(iterations=800), workdir
    )
    assert execution.engine_version == "v1912"
    assert all(c["passed"] for c in result.crosschecks), result.crosschecks
    assert metric(result, "cfd.max_speed").value > 6.0  # flow accelerates around obstacles
    assert metric(result, "cfd.mesh_cells").value == prepared.summary["cells"]


def test_overlapping_obstacles_rejected():
    from parallel_contracts.domain.cfd import CfdObstacle
    from parallel_engines.registry import get_adapter

    sc = cfd_scenario(added_obstacles=[CfdObstacle(id="x", name="overlap", x0=45, x1=55, y0=45, y1=55)])
    report = get_adapter("openfoam").validate_scenario(site_wind_model(), sc)
    assert not report.ok and any(i.code == "overlapping_obstacles" for i in report.errors)


def test_block_mesh_cell_count_matches_grid():
    m = site_wind_model().content
    _, cells = block_mesh_dict(m, m.obstacles)
    assert cells == 160 * 100 - 20 * 20 - 2 * 32
