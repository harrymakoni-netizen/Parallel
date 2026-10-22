import math

import pytest
from pydantic import ValidationError

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.common import Distribution
from parallel_contracts.domain.drainage import DrainageNetworkModel
from parallel_contracts.domain.pedestrian import PedestrianControls
from parallel_contracts.geometry import PolygonGeom, rect
from parallel_contracts.provenance import Provenance, ProvenanceKind, find_unadopted_ai_values
from parallel_contracts.runs import LEGAL_TRANSITIONS, IllegalTransition, RunState, check_transition
from parallel_contracts.scenario import ScenarioContent
from parallel_contracts.scene import SceneRevisionContent, VisualAsset
from parallel_contracts.units import UnitError, convert, convert_difference, to_si


class TestUnits:
    def test_round_trip_conversions(self):
        for value, a, b in [(1.0, "km", "m"), (36.0, "km/h", "m/s"), (2.5, "ha", "m2"), (20.0, "degC", "K")]:
            assert math.isclose(convert(convert(value, a, b), b, a), value, rel_tol=1e-12)

    def test_known_values(self):
        assert math.isclose(convert(36.0, "km/h", "m/s"), 10.0)
        assert math.isclose(convert(1.0, "ha", "m2"), 10_000.0)
        assert math.isclose(to_si(25.0, "degC"), 298.15)
        assert math.isclose(convert(60.0, "mm/h", "mm/min"), 1.0)
        assert math.isclose(convert(1.0, "kWh", "MJ"), 3.6)

    def test_rainfall_intensity_is_not_a_velocity(self):
        # Same physical dimension, deliberately different semantic dimension.
        with pytest.raises(UnitError):
            convert(10.0, "mm/h", "m/s")

    def test_temperature_difference_ignores_offset(self):
        assert math.isclose(convert_difference(10.0, "degC", "K"), 10.0)

    def test_incompatible_dimensions_rejected(self):
        with pytest.raises(UnitError):
            convert(1.0, "m", "s")

    def test_unknown_unit_rejected(self):
        with pytest.raises(UnitError):
            convert(1.0, "furlong", "m")


class TestProvenance:
    def test_adoption_only_for_ai(self):
        with pytest.raises(ValidationError):
            Provenance(kind=ProvenanceKind.measured, adopted_by="u1")

    def test_find_unadopted_ai_values(self):
        data = {
            "a": {"value": 1, "provenance": {"kind": "ai_suggested"}},
            "b": [{"provenance": {"kind": "ai_suggested", "adopted_by": "user-1"}}],
            "c": {"provenance": {"kind": "measured"}},
        }
        assert find_unadopted_ai_values(data) == ["$.a.provenance"]


class TestGeometry:
    def test_unclosed_ring_rejected(self):
        with pytest.raises(ValidationError):
            PolygonGeom(coordinates=[[[0, 0], [1, 0], [1, 1], [0, 1]]])

    def test_non_finite_rejected(self):
        with pytest.raises(ValidationError):
            PolygonGeom(coordinates=[[[0, 0], [float("nan"), 0], [1, 1], [0, 0]]])

    def test_rect(self):
        assert rect(0, 0, 2, 1).coordinates[0][0] == [0, 0]


class TestRunStates:
    def test_terminal_states_have_no_exit(self):
        for s in (RunState.completed, RunState.failed, RunState.cancelled):
            assert LEGAL_TRANSITIONS[s] == frozenset()

    def test_external_run_cannot_jump_to_running(self):
        with pytest.raises(IllegalTransition):
            check_transition(RunState.awaiting_external_result, RunState.running)

    def test_happy_path(self):
        path = [
            RunState.draft,
            RunState.validating,
            RunState.queued,
            RunState.preparing,
            RunState.running,
            RunState.collecting,
            RunState.postprocessing,
            RunState.completed,
        ]
        for a, b in zip(path, path[1:], strict=False):
            check_transition(a, b)


class TestSceneRules:
    def test_generated_content_rejected_in_captured_scene(self):
        va = VisualAsset(id="v", asset_id="a", kind="gaussian_splat", format="ply", generated=True)
        with pytest.raises(ValidationError, match="Generated visual content"):
            SceneRevisionContent(scene_id="s", visual_assets=[va])
        SceneRevisionContent(scene_id="s", visual_assets=[va], hypothetical=True)


class TestDomainRules:
    def test_drainage_requires_vertical_reference(self):
        base = {
            "vertical_reference": "unknown",
            "rainfall": [{"id": "r", "name": "r", "interval_min": 5, "intensities_mm_per_h": [10]}],
            "raingage_series": {"G": "r"},
            "outfalls": [{"id": "O", "position": [0, 0], "invert_elev_m": 0}],
            "conduits": [
                {
                    "id": "C",
                    "from_node": "J",
                    "to_node": "O",
                    "length_m": 10,
                    "roughness_n": 0.013,
                    "geom1_m": 0.3,
                }
            ],
        }
        with pytest.raises(ValidationError, match="vertical reference"):
            DrainageNetworkModel(**base)
        DrainageNetworkModel(**{**base, "vertical_reference": "Local site datum (synthetic)"})

    def test_timed_closures_disabled(self):
        with pytest.raises(ValidationError, match="Timed closures"):
            PedestrianControls(timed_closures=[{"segment": "x", "at_s": 10}])

    def test_distribution_parameters_required(self):
        with pytest.raises(ValidationError):
            Distribution(kind="normal", mean=1.0, unit="m/s")

    def test_envelope_discipline_must_match(self):
        content = {
            "model_kind": "pedestrian_geometry",
            "walkable_area": rect(0, 0, 10, 10).model_dump(),
            "exits": [{"id": "e", "name": "e", "polygon": rect(9, 4, 10, 6).model_dump()}],
        }
        DomainModelEnvelope(discipline="pedestrian", name="m", content=content)
        with pytest.raises(ValidationError):
            DomainModelEnvelope(discipline="traffic", name="m", content=content)

    def test_scenario_controls_must_match_discipline(self):
        with pytest.raises(ValidationError):
            ScenarioContent(discipline="drainage", engine_id="swmm", controls={"kind": "pedestrian"})
