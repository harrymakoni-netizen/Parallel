import math

import numpy as np
import pytest

from parallel_contracts.coordinates import GeoReference, Transform
from parallel_contracts.geometry import PolygonGeom, rect
from parallel_contracts.provenance import Provenance, SourcedQuantity
from parallel_contracts.scene import Measurement, MeasurementTolerance
from parallel_scene import georef, ply
from parallel_scene.geometry import component_index, sample_points_in_polygon, validate_polygon, walkable_area
from parallel_scene.measurement import evaluate
from parallel_scene.networks import Graph
from parallel_scene.transforms import (
    FrameGraph,
    apply,
    decompose_similarity,
    invert,
    matrix_to_quat,
    quat_to_matrix,
    scale_from_reference,
    similarity,
    to_list,
    umeyama,
)

RNG = np.random.default_rng(7)


class TestTransforms:
    def test_round_trip(self):
        q = np.array([0.9, 0.1, -0.3, 0.2])
        q /= np.linalg.norm(q)
        m = similarity(2.5, q, [10, -4, 1.5])
        pts = RNG.normal(size=(50, 3)) * 20
        back = apply(invert(m), apply(m, pts))
        assert np.allclose(back, pts, atol=1e-9)

    def test_quaternion_matrix_round_trip(self):
        for _ in range(20):
            q = RNG.normal(size=4)
            q /= np.linalg.norm(q)
            q = q if q[0] >= 0 else -q
            assert np.allclose(matrix_to_quat(quat_to_matrix(q)), q, atol=1e-9)

    def test_quaternion_convention_is_wxyz_hamilton(self):
        # 90 degrees about +Z maps +X to +Y
        q = [math.cos(math.pi / 4), 0, 0, math.sin(math.pi / 4)]
        assert np.allclose(quat_to_matrix(q) @ [1, 0, 0], [0, 1, 0], atol=1e-12)

    def test_decompose(self):
        m = similarity(0.5, [1, 0, 0, 0], [1, 2, 3])
        s, q, t = decompose_similarity(m)
        assert math.isclose(s, 0.5) and np.allclose(q, [1, 0, 0, 0]) and np.allclose(t, [1, 2, 3])

    def test_decompose_rejects_shear(self):
        m = np.eye(4)
        m[0, 1] = 0.3
        with pytest.raises(ValueError):
            decompose_similarity(m)

    def test_umeyama_recovers_similarity(self):
        q = np.array([0.7, 0.2, 0.1, -0.68])
        q /= np.linalg.norm(q)
        truth = similarity(3.2, q, [5, 6, -2])
        src = RNG.uniform(-5, 5, size=(30, 3))
        dst = apply(truth, src)
        est, rms = umeyama(src, dst)
        assert rms < 1e-9
        assert np.allclose(est, truth, atol=1e-8)

    def test_umeyama_rejects_collinear(self):
        src = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]], float)
        with pytest.raises(ValueError):
            umeyama(src, src * 2)

    def test_scale_from_reference(self):
        assert math.isclose(scale_from_reference([0, 0, 0], [0, 2, 0], 5.0), 2.5)
        with pytest.raises(ValueError):
            scale_from_reference([1, 1, 1], [1, 1, 1], 5.0)

    def test_frame_graph_chains_and_inverts(self):
        a_to_b = similarity(2.0, [1, 0, 0, 0], [1, 0, 0])
        b_to_c = similarity(1.0, [math.cos(0.3), 0, 0, math.sin(0.3)], [0, 3, 0])
        g = FrameGraph(
            [
                Transform(from_frame="capture", to_frame="canonical", matrix=to_list(a_to_b)),
                Transform(from_frame="canonical", to_frame="engine", matrix=to_list(b_to_c)),
            ]
        )
        p = np.array([0.3, 0.4, 0.5])
        direct = apply(b_to_c @ a_to_b, p)
        assert np.allclose(apply(g.resolve("capture", "engine"), p), direct)
        assert np.allclose(apply(g.resolve("engine", "capture"), direct), p)
        with pytest.raises(KeyError):
            g.resolve("capture", "nowhere")


class TestGeoreference:
    GEO = GeoReference(
        horizontal_crs="EPSG:32735",  # UTM 35S (metres)
        local_origin_projected=[600000.0, 7100000.0],
        local_rotation_deg=30.0,
        vertical_reference="unknown",
    )

    def test_local_projected_round_trip(self):
        pts = RNG.uniform(-500, 500, size=(20, 2))
        back = georef.projected_to_local(self.GEO, georef.local_to_projected(self.GEO, pts))
        assert np.allclose(back, pts, atol=1e-9)

    def test_local_lonlat_round_trip_sub_millimetre(self):
        pts = RNG.uniform(-2000, 2000, size=(20, 2))
        back = georef.lonlat_to_local(self.GEO, georef.local_to_lonlat(self.GEO, pts))
        assert np.max(np.abs(back - pts)) < 1e-3

    def test_rotation_preserves_distances(self):
        a, b = np.array([[0, 0]]), np.array([[30, 40]])
        ea, eb = georef.local_to_projected(self.GEO, a), georef.local_to_projected(self.GEO, b)
        assert math.isclose(float(np.linalg.norm(eb - ea)), 50.0, abs_tol=1e-6)

    def test_geographic_crs_rejected_for_metric_work(self):
        with pytest.raises(georef.GeoReferenceError, match="not a projected CRS"):
            georef.check_projected("EPSG:4326")

    def test_unknown_crs_rejected(self):
        with pytest.raises(georef.GeoReferenceError):
            georef.check_projected("EPSG:999999")

    def test_utm_suggestion(self):
        assert georef.utm_crs_for(28.0, -26.2) == "EPSG:32735"
        assert georef.utm_crs_for(-0.1, 51.5) == "EPSG:32630"


class TestGeometry:
    def test_self_intersecting_polygon_rejected(self):
        bowtie = PolygonGeom(coordinates=[[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]])
        issues = validate_polygon(bowtie, "walkable_area", "Walkable area")
        assert issues and issues[0].code == "self_intersection"

    def test_obstacle_exclusion(self):
        res = walkable_area(rect(0, 0, 10, 10), [rect(4, 4, 6, 6)])
        assert math.isclose(res.area.area, 96.0)
        assert component_index(res.components, (5, 5)) is None
        assert component_index(res.components, (1, 1)) == 0

    def test_closure_splits_into_components(self):
        corridor = rect(0, 0, 30, 3)
        res = walkable_area(corridor, [rect(14, -1, 16, 4)])
        assert len(res.components) == 2
        left = component_index(res.components, (1, 1))
        right = component_index(res.components, (29, 1))
        assert left is not None and right is not None and left != right

    def test_point_sampling_respects_spacing_and_is_deterministic(self):
        poly = walkable_area(rect(0, 0, 10, 5), []).components[0]
        a = sample_points_in_polygon(poly, 30, np.random.default_rng(1), 0.5)
        b = sample_points_in_polygon(poly, 30, np.random.default_rng(1), 0.5)
        assert a == b
        d = np.linalg.norm(np.array(a)[:, None] - np.array(a)[None], axis=-1)
        assert d[np.triu_indices(30, 1)].min() >= 0.5

    def test_overfull_zone_raises(self):
        poly = walkable_area(rect(0, 0, 1, 1), []).components[0]
        with pytest.raises(ValueError, match="could only place"):
            sample_points_in_polygon(poly, 100, np.random.default_rng(1), 0.5, max_attempts=5000)


class TestNetworks:
    def test_reachability(self):
        g = Graph()
        g.add_edge("J1", "J2")
        g.add_edge("J2", "OUT")
        g.add_node("ISOLATED")
        assert g.reaches_any("J1", ["OUT"])
        assert not g.reaches_any("ISOLATED", ["OUT"])
        assert len(g.undirected_components()) == 2


class TestMeasurement:
    def test_errors_and_tolerance(self):
        prov = Provenance(kind="measured", source="tape")
        m = Measurement(
            id="m1",
            label="door width",
            purpose="independent_check",
            point_a=[0, 0, 0],
            point_b=[0.95, 0, 0],
            reference_distance=SourcedQuantity(value=100, unit="cm", provenance=prov),
            source="picked points",
        )
        tol = MeasurementTolerance(purpose="room-scale demonstration", max_abs_error_m=0.03)
        out = evaluate(m, tol)
        assert math.isclose(out.reconstructed_distance_m, 0.95)
        assert math.isclose(out.abs_error_m, 0.05)
        assert math.isclose(out.rel_error, 0.05)
        assert out.within_tolerance is False
        assert "forensic measurement" in tol.not_suitable_for


class TestPly:
    def test_point_cloud_round_trip(self, tmp_path):
        xyz = RNG.normal(size=(100, 3)).astype(np.float32)
        rgb = RNG.integers(0, 255, size=(100, 3))
        p = tmp_path / "pc.ply"
        ply.write_point_cloud(p, xyz, rgb, comments=["test"])
        h, data = ply.read_vertices(p)
        assert h.vertex_count == 100 and not h.is_gaussian_splat and h.has_colour
        assert np.allclose(np.column_stack([data["x"], data["y"], data["z"]]), xyz)

    def test_gaussian_detection(self, tmp_path):
        n = 10
        p = tmp_path / "g.ply"
        ply.write_gaussians(
            p, RNG.normal(size=(n, 3)), np.zeros((n, 3)), np.zeros(n), np.full((n, 3), -3.0), np.tile([1, 0, 0, 0], (n, 1))
        )
        h, _ = ply.read_vertices(p)
        assert h.is_gaussian_splat

    def test_truncated_file_rejected(self, tmp_path):
        p = tmp_path / "pc.ply"
        ply.write_point_cloud(p, RNG.normal(size=(100, 3)))
        data = p.read_bytes()
        p.write_bytes(data[:-50])
        with pytest.raises(ply.PlyError, match="truncated"):
            ply.read_vertices(p)

    def test_non_ply_rejected(self, tmp_path):
        p = tmp_path / "x.ply"
        p.write_bytes(b"not a ply")
        with pytest.raises(ply.PlyError):
            ply.read_header(p)

    def test_sh_colour_round_trip(self):
        rgb = RNG.uniform(0, 1, size=(5, 3))
        assert np.allclose(ply.sh_dc_to_rgb(ply.rgb_to_sh_dc(rgb)), rgb)
