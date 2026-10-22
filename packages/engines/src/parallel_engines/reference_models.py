"""Small, clearly synthetic reference models for each engine's reference workflow.

These models are synthetic demonstration material (provenance ``synthetic``).
They exercise real engine execution in tests and seed the demonstration
projects; none of them represents a real site.
"""

from __future__ import annotations

from parallel_contracts.domain import DomainModelEnvelope
from parallel_contracts.domain.common import Distribution, NamedPolygon
from parallel_contracts.domain.pedestrian import (
    ExitDef,
    MovementParameters,
    PedestrianControls,
    PedestrianModel,
    RouteSegment,
    StartGroup,
)
from parallel_contracts.geometry import MultiPolygonGeom, rect
from parallel_contracts.provenance import Provenance, ProvenanceKind
from parallel_contracts.scenario import RunSettings, ScenarioContent

SYN = Provenance(kind=ProvenanceKind.synthetic, note="Synthetic reference model; not a real site.")


def _union(*rects) -> MultiPolygonGeom:
    import shapely

    from parallel_scene.geometry import to_shapely

    u = shapely.union_all([to_shapely(r) for r in rects])
    polys = [u] if u.geom_type == "Polygon" else list(u.geoms)
    return MultiPolygonGeom(
        coordinates=[
            [[list(c) for c in p.exterior.coords], *[[list(c) for c in r.coords] for r in p.interiors]]
            for p in polys
        ]
    )


def mine_section_model() -> DomainModelEnvelope:
    """A level mine section: 80 m main drift, a north crosscut to a refuge chamber, portals east and west."""
    walk = _union(rect(0, 0, 80, 4), rect(38, 4, 42, 20), rect(34, 20, 46, 28))
    walk_poly = walk.coordinates[0]
    content = PedestrianModel(
        walkable_area={"type": "Polygon", "coordinates": walk_poly},
        obstacles=[
            NamedPolygon(id="loader", name="Parked loader", polygon=rect(50, 1.4, 54, 2.6), provenance=SYN)
        ],
        exits=[
            ExitDef(id="west_portal", name="West portal", polygon=rect(0, 0, 1, 4), provenance=SYN),
            ExitDef(id="east_portal", name="East portal", polygon=rect(79, 0, 80, 4), provenance=SYN),
            ExitDef(
                id="refuge",
                name="Refuge chamber",
                kind="refuge",
                capacity=40,
                polygon=rect(36, 26, 44, 28),
                provenance=SYN,
            ),
        ],
        start_groups=[
            StartGroup(
                id="face_crew", name="Face crew", zone=rect(25, 0.5, 35, 3.5), count=30, provenance=SYN
            ),
            StartGroup(
                id="workshop_crew",
                name="Workshop crew",
                zone=rect(66, 0.5, 76, 3.5),
                count=20,
                provenance=SYN,
            ),
            StartGroup(
                id="development_crew",
                name="Development crew",
                zone=rect(38.5, 11, 41.5, 18),
                count=10,
                provenance=SYN,
            ),
        ],
        route_segments=[
            RouteSegment(id="west_drift", name="West drift (x 18-20 m)", polygon=rect(18, -1, 20, 5)),
            RouteSegment(id="east_drift", name="East drift (x 60-62 m)", polygon=rect(60, -1, 62, 5)),
            RouteSegment(id="crosscut", name="Crosscut (y 6-8 m)", polygon=rect(37, 6, 43, 8)),
        ],
        movement=MovementParameters(),
    )
    return DomainModelEnvelope(
        discipline="pedestrian", name="Level mine section (synthetic)", content=content, provenance=SYN
    )


def pedestrian_scenario(**controls) -> ScenarioContent:
    seed = controls.pop("seed", 42)
    baseline = controls.pop("is_baseline", False)
    return ScenarioContent(
        discipline="pedestrian",
        engine_id="jupedsim",
        is_baseline=baseline,
        controls=PedestrianControls(**controls),
        settings=RunSettings(seed=seed, timeout_s=600),
    )


def premovement(low: float, high: float) -> Distribution:
    return Distribution(
        kind="uniform",
        low=low,
        high=high,
        unit="s",
        provenance=Provenance(kind=ProvenanceKind.assumption, note="Pre-movement time range assumption"),
    )


# ---------------------------------------------------------------- traffic (SUMO)
def crossroads_model() -> DomainModelEnvelope:
    """Four-arm intersection with a north-east bypass (synthetic)."""
    from parallel_contracts.domain.road import DemandFlow, RoadEdge, RoadNetworkModel, RoadNode

    pts = {
        "W0": (-600, 0),
        "W": (-300, 0),
        "C": (0, 0),
        "E": (300, 0),
        "E0": (600, 0),
        "N": (0, 300),
        "N0": (0, 600),
        "S": (0, -300),
        "S0": (0, -600),
        "NE": (300, 300),
    }
    nodes = [RoadNode(id=k, x=x, y=y, type="priority") for k, (x, y) in pts.items()]
    pairs = [
        ("W0", "W"),
        ("W", "C"),
        ("C", "E"),
        ("E", "E0"),
        ("N0", "N"),
        ("N", "C"),
        ("C", "S"),
        ("S", "S0"),
        ("N", "NE"),
        ("NE", "E"),
    ]
    edges = []
    for a, b in pairs:
        for x, y in ((a, b), (b, a)):
            bypass = {x, y} <= {"N", "NE", "E"}
            edges.append(
                RoadEdge(
                    id=f"{x}_{y}",
                    from_node=x,
                    to_node=y,
                    num_lanes=1 if bypass else 2,
                    speed_mps=11.11 if bypass else 13.89,
                    priority=1 if bypass else 2,
                    provenance=SYN,
                )
            )
    demand_prov = Provenance(
        kind=ProvenanceKind.assumption, note="Synthetic, uncalibrated demand for the reference workflow"
    )
    demand = [
        DemandFlow(
            id="we",
            from_edge="W0_W",
            to_edge="E_E0",
            begin_s=0,
            end_s=900,
            number=150,
            provenance=demand_prov,
        ),
        DemandFlow(
            id="ew",
            from_edge="E0_E",
            to_edge="W_W0",
            begin_s=0,
            end_s=900,
            number=100,
            provenance=demand_prov,
        ),
        DemandFlow(
            id="ns",
            from_edge="N0_N",
            to_edge="S_S0",
            begin_s=0,
            end_s=900,
            number=120,
            provenance=demand_prov,
        ),
        DemandFlow(
            id="sn", from_edge="S0_S", to_edge="N_N0", begin_s=0, end_s=900, number=80, provenance=demand_prov
        ),
    ]
    content = RoadNetworkModel(nodes=nodes, edges=edges, demand=demand, step_length_s=0.5)
    return DomainModelEnvelope(
        discipline="traffic", name="Crossroads with bypass (synthetic)", content=content, provenance=SYN
    )


def traffic_scenario(**controls) -> ScenarioContent:
    from parallel_contracts.domain.road import TrafficControls

    seed = controls.pop("seed", 42)
    baseline = controls.pop("is_baseline", False)
    controls.setdefault("end_s", 1800)
    return ScenarioContent(
        discipline="traffic",
        engine_id="sumo",
        is_baseline=baseline,
        controls=TrafficControls(**controls),
        settings=RunSettings(seed=seed, timeout_s=600),
    )


# ---------------------------------------------------------------- drainage (SWMM)
def design_storm(peak_mm_h: float = 90.0, duration_min: int = 60, interval_min: int = 5) -> list[float]:
    """Symmetric triangular hyetograph (synthetic design-storm shape)."""
    n = duration_min // interval_min
    half = (n - 1) / 2
    return [round(peak_mm_h * (1 - abs(i - half) / (half + 1)), 3) for i in range(n)]


def drainage_model() -> DomainModelEnvelope:
    """Small separate storm system: 3 subcatchments, 4 junctions, 1 outfall (synthetic)."""
    from parallel_contracts.domain.drainage import (
        Conduit,
        DrainageNetworkModel,
        Junction,
        Outfall,
        RainfallSeries,
        Subcatchment,
    )

    rain_prov = Provenance(
        kind=ProvenanceKind.assumption, note="Synthetic triangular design storm, not a recorded event"
    )
    inv = Provenance(
        kind=ProvenanceKind.assumption, note="Invert levels and pipe sizes assumed (hidden infrastructure)"
    )
    content = DrainageNetworkModel(
        vertical_reference="Local site datum (synthetic; z = 100 m at J1 rim)",
        start_datetime="01/01/2026 00:00:00",
        end_datetime="01/01/2026 04:00:00",
        report_step_s=60,
        wet_step_s=60,
        dry_step_s=600,
        routing_step_s=5,
        rainfall=[
            RainfallSeries(
                id="storm_10y",
                name="Design storm A (synthetic)",
                interval_min=5,
                intensities_mm_per_h=design_storm(90),
                provenance=rain_prov,
            ),
            RainfallSeries(
                id="storm_2y",
                name="Design storm B (synthetic)",
                interval_min=5,
                intensities_mm_per_h=design_storm(45),
                provenance=rain_prov,
            ),
        ],
        raingage_series={"RG1": "storm_10y"},
        subcatchments=[
            Subcatchment(
                id="S1",
                raingage="RG1",
                outlet="J1",
                area_ha=3.0,
                imperv_pct=70,
                width_m=300,
                slope_pct=1.0,
                provenance=SYN,
            ),
            Subcatchment(
                id="S2",
                raingage="RG1",
                outlet="J2",
                area_ha=4.0,
                imperv_pct=60,
                width_m=400,
                slope_pct=0.8,
                provenance=SYN,
            ),
            Subcatchment(
                id="S3",
                raingage="RG1",
                outlet="J3",
                area_ha=2.5,
                imperv_pct=80,
                width_m=250,
                slope_pct=1.2,
                provenance=SYN,
            ),
        ],
        junctions=[
            Junction(id="J1", position=[0, 300], invert_elev_m=98.0, max_depth_m=2.0, provenance=inv),
            Junction(id="J2", position=[150, 220], invert_elev_m=97.4, max_depth_m=2.2, provenance=inv),
            Junction(id="J3", position=[300, 150], invert_elev_m=96.8, max_depth_m=2.4, provenance=inv),
            Junction(id="J4", position=[450, 80], invert_elev_m=96.2, max_depth_m=2.6, provenance=inv),
        ],
        outfalls=[Outfall(id="OUT1", position=[600, 0], invert_elev_m=95.5, provenance=inv)],
        conduits=[
            Conduit(
                id="C1",
                from_node="J1",
                to_node="J2",
                length_m=170,
                roughness_n=0.013,
                geom1_m=0.45,
                provenance=inv,
            ),
            Conduit(
                id="C2",
                from_node="J2",
                to_node="J3",
                length_m=165,
                roughness_n=0.013,
                geom1_m=0.6,
                provenance=inv,
            ),
            Conduit(
                id="C3",
                from_node="J3",
                to_node="J4",
                length_m=165,
                roughness_n=0.013,
                geom1_m=0.6,
                provenance=inv,
            ),
            Conduit(
                id="C4",
                from_node="J4",
                to_node="OUT1",
                length_m=170,
                roughness_n=0.013,
                geom1_m=0.75,
                provenance=inv,
            ),
        ],
    )
    return DomainModelEnvelope(
        discipline="drainage", name="Storm drainage network (synthetic)", content=content, provenance=SYN
    )


def drainage_scenario(**controls) -> ScenarioContent:
    from parallel_contracts.domain.drainage import DrainageControls

    baseline = controls.pop("is_baseline", False)
    return ScenarioContent(
        discipline="drainage",
        engine_id="swmm",
        is_baseline=baseline,
        controls=DrainageControls(**controls),
        settings=RunSettings(seed=None, timeout_s=600),
    )


# ---------------------------------------------------------------- water supply (EPANET)
def water_network_model() -> DomainModelEnvelope:
    """Looped distribution network fed by a reservoir and a tank (synthetic)."""
    from parallel_contracts.domain.water_supply import (
        DemandPattern,
        WaterJunction,
        WaterNetworkModel,
        WaterPipe,
        WaterReservoir,
        WaterTank,
    )

    j = lambda i, x, y, z, q: WaterJunction(
        id=i,
        position=[x, y],
        elevation_m=z,
        base_demand_lps=q,  # noqa: E731
        pattern_id="domestic",
        provenance=SYN,
    )
    content = WaterNetworkModel(
        vertical_reference="Local site datum (synthetic)",
        reservoirs=[WaterReservoir(id="R1", position=[-200, 0], head_m=70.0, provenance=SYN)],
        tanks=[
            WaterTank(
                id="T1",
                position=[400, 300],
                elevation_m=55.0,
                init_level_m=4.0,
                min_level_m=0.5,
                max_level_m=8.0,
                diameter_m=15.0,
                provenance=SYN,
            )
        ],
        junctions=[
            j("J1", 0, 0, 20, 4.0),
            j("J2", 200, 0, 22, 5.0),
            j("J3", 400, 0, 25, 3.0),
            j("J4", 0, 200, 24, 4.5),
            j("J5", 200, 200, 27, 6.0),
            j("J6", 400, 200, 30, 3.5),
        ],
        pipes=[
            WaterPipe(id="P0", node1="R1", node2="J1", length_m=200, diameter_mm=300, provenance=SYN),
            WaterPipe(id="P1", node1="J1", node2="J2", length_m=200, diameter_mm=200, provenance=SYN),
            WaterPipe(id="P2", node1="J2", node2="J3", length_m=200, diameter_mm=150, provenance=SYN),
            WaterPipe(id="P3", node1="J1", node2="J4", length_m=200, diameter_mm=200, provenance=SYN),
            WaterPipe(id="P4", node1="J2", node2="J5", length_m=200, diameter_mm=150, provenance=SYN),
            WaterPipe(id="P5", node1="J3", node2="J6", length_m=200, diameter_mm=150, provenance=SYN),
            WaterPipe(id="P6", node1="J4", node2="J5", length_m=200, diameter_mm=150, provenance=SYN),
            WaterPipe(id="P7", node1="J5", node2="J6", length_m=200, diameter_mm=150, provenance=SYN),
            WaterPipe(id="P8", node1="J6", node2="T1", length_m=100, diameter_mm=200, provenance=SYN),
        ],
        patterns=[
            DemandPattern(
                id="domestic",
                multipliers=[
                    0.6,
                    0.5,
                    0.5,
                    0.6,
                    0.8,
                    1.2,
                    1.5,
                    1.4,
                    1.2,
                    1.0,
                    1.0,
                    1.1,
                    1.2,
                    1.1,
                    1.0,
                    1.0,
                    1.1,
                    1.3,
                    1.5,
                    1.4,
                    1.2,
                    1.0,
                    0.8,
                    0.7,
                ],
            )
        ],
        duration_s=24 * 3600,
        hydraulic_step_s=3600,
        pattern_step_s=3600,
        report_step_s=3600,
    )
    return DomainModelEnvelope(
        discipline="water_supply", name="Looped water network (synthetic)", content=content, provenance=SYN
    )


def water_scenario(**controls) -> ScenarioContent:
    from parallel_contracts.domain.water_supply import WaterSupplyControls

    return ScenarioContent(
        discipline="water_supply",
        engine_id="epanet",
        is_baseline=controls.pop("is_baseline", False),
        controls=WaterSupplyControls(**controls),
        settings=RunSettings(timeout_s=300),
    )


# ---------------------------------------------------------------- operations (SimPy)
def site_operations_model() -> DomainModelEnvelope:
    """Construction site deliveries: gate check, unloading at a bay, crane lift (synthetic)."""
    from parallel_contracts.domain.operations import (
        OperationsModel,
        OpsArrival,
        OpsFlow,
        OpsResource,
        OpsStep,
    )

    tri = lambda lo, mode, hi: Distribution(kind="triangular", low=lo, mode=mode, high=hi, unit="min")  # noqa: E731
    content = OperationsModel(
        horizon_s=10 * 3600,
        resources=[
            OpsResource(id="gate", name="Site gate", kind="gate", capacity=1, provenance=SYN),
            OpsResource(id="bay", name="Loading bay", kind="loading_bay", capacity=1, provenance=SYN),
            OpsResource(id="crane", name="Tower crane", kind="crane", capacity=1, provenance=SYN),
        ],
        flows=[
            OpsFlow(
                id="materials",
                name="Material deliveries",
                entity_type="truck",
                arrival=OpsArrival(kind="poisson", mean_interarrival_s=20 * 60, end_s=8 * 3600),
                steps=[
                    OpsStep(id="gate", name="Gate check", resource_id="gate", duration=tri(2, 3, 6)),
                    OpsStep(id="unload", name="Unload", resource_id="bay", duration=tri(10, 15, 30)),
                    OpsStep(id="lift", name="Crane lift", resource_id="crane", duration=tri(5, 8, 15)),
                ],
                provenance=SYN,
            ),
            OpsFlow(
                id="concrete",
                name="Concrete trucks",
                entity_type="truck",
                arrival=OpsArrival(kind="fixed_interval", interval_s=45 * 60, start_s=3600, end_s=6 * 3600),
                steps=[
                    OpsStep(id="gate", name="Gate check", resource_id="gate", duration=tri(2, 3, 5)),
                    OpsStep(id="pour", name="Pump pour", resource_id="bay", duration=tri(20, 25, 35)),
                ],
                provenance=SYN,
            ),
        ],
    )
    return DomainModelEnvelope(
        discipline="operations", name="Site deliveries (synthetic)", content=content, provenance=SYN
    )


def operations_scenario(**controls) -> ScenarioContent:
    from parallel_contracts.domain.operations import OperationsControls

    seed = controls.pop("seed", 7)
    return ScenarioContent(
        discipline="operations",
        engine_id="simpy",
        is_baseline=controls.pop("is_baseline", False),
        controls=OperationsControls(**controls),
        settings=RunSettings(seed=seed, timeout_s=300),
    )


# ---------------------------------------------------------------- CFD (OpenFOAM)
def site_wind_model() -> DomainModelEnvelope:
    """Plan-view wind flow around a site building and hoarding (synthetic)."""
    from parallel_contracts.domain.cfd import CfdObstacle, CfdStudyModel

    content = CfdStudyModel(
        domain_x0=0,
        domain_x1=160,
        domain_y0=0,
        domain_y1=100,
        obstacles=[
            CfdObstacle(id="building", name="Existing building", x0=40, x1=60, y0=40, y1=60),
            CfdObstacle(id="hoarding", name="Site hoarding", x0=90, x1=92, y0=30, y1=62),
        ],
        inlet_speed_mps=6.0,
        target_cell_size_m=1.0,
        turbulent_length_scale_m=2.0,
        provenance=Provenance(kind=ProvenanceKind.assumption, note="Synthetic geometry and wind speed"),
    )
    return DomainModelEnvelope(
        discipline="cfd", name="Site wind study (synthetic)", content=content, provenance=SYN
    )


def cfd_scenario(**controls) -> ScenarioContent:
    from parallel_contracts.domain.cfd import CfdControls

    return ScenarioContent(
        discipline="cfd",
        engine_id="openfoam",
        is_baseline=controls.pop("is_baseline", False),
        controls=CfdControls(**controls),
        settings=RunSettings(timeout_s=1200),
    )


# ---------------------------------------------------------------- thermal (EnergyPlus / OpenStudio)
def office_thermal_model() -> DomainModelEnvelope:
    from parallel_contracts.domain.thermal import DesignDay, ThermalModel, ThermalZoneDef

    content = ThermalModel(
        zones=[
            ThermalZoneDef(
                id="z1",
                name="Office block",
                footprint=rect(0, 0, 30, 15),
                floor_to_floor_m=3.5,
                num_floors=2,
                provenance=SYN,
            )
        ],
        design_days=[
            DesignDay(
                name="Summer design day (synthetic)",
                month=1,
                day=21,
                day_type="SummerDesignDay",
                max_dry_bulb_c=32.0,
                daily_range_c=12.0,
                humidity_wet_bulb_c=19.0,
                provenance=SYN,
            ),
            DesignDay(
                name="Winter design day (synthetic)",
                month=7,
                day=21,
                day_type="WinterDesignDay",
                max_dry_bulb_c=2.0,
                daily_range_c=0.0,
                humidity_wet_bulb_c=0.0,
                provenance=SYN,
            ),
        ],
        latitude_deg=-26.2,
        longitude_deg=28.0,
        time_zone_h=2.0,
        elevation_m=1750.0,
    )
    return DomainModelEnvelope(
        discipline="thermal", name="Two-storey office (synthetic)", content=content, provenance=SYN
    )


def thermal_scenario(**controls) -> ScenarioContent:
    from parallel_contracts.domain.thermal import ThermalControls

    return ScenarioContent(
        discipline="thermal",
        engine_id="energyplus",
        controls=ThermalControls(**controls),
        settings=RunSettings(timeout_s=600),
    )


# ---------------------------------------------------------------- fire (FDS)
def room_fire_model() -> DomainModelEnvelope:
    from parallel_contracts.domain.fire import (
        FdsDevice,
        FdsMesh,
        FdsObstruction,
        FdsSurface,
        FdsVent,
        FireModel,
    )

    content = FireModel(
        chid="room_fire",
        title="Synthetic room fire",
        t_end_s=120,
        meshes=[FdsMesh(id="m1", ijk=[40, 30, 25], xb=[0, 4, 0, 3, 0, 2.5])],
        surfaces=[
            FdsSurface(
                id="BURNER",
                hrrpua_kw_m2=500,
                ramp_up_s=60,
                color="RED",
                provenance=Provenance(kind=ProvenanceKind.assumption, note="Design fire assumption"),
            )
        ],
        obstructions=[FdsObstruction(id="burner_base", xb=[1.8, 2.2, 1.3, 1.7, 0, 0.2])],
        vents=[
            FdsVent(id="burner_top", xb=[1.8, 2.2, 1.3, 1.7, 0.2, 0.2], surf_id="BURNER"),
            FdsVent(id="door", xb=[4, 4, 1.0, 2.0, 0, 2.0], surf_id="OPEN"),
        ],
        devices=[
            FdsDevice(id="TC_ceiling", quantity="TEMPERATURE", xyz=[2.0, 1.5, 2.4]),
            FdsDevice(id="VIS_door", quantity="VISIBILITY", xyz=[3.8, 1.5, 1.8]),
        ],
    )
    return DomainModelEnvelope(
        discipline="fire", name="Room fire (synthetic)", content=content, provenance=SYN
    )


# ---------------------------------------------------------------- rigid body (Isaac Sim)
def rigid_body_reference() -> DomainModelEnvelope:
    from .adapters.isaacsim import reference_scene

    return DomainModelEnvelope(
        discipline="rigid_body", name="Reference box drop", content=reference_scene(), provenance=SYN
    )


# ---------------------------------------------------------------- ventilation (Ventsim exchange)
def mine_ventilation_model() -> DomainModelEnvelope:
    from parallel_contracts.domain.ventilation import Airway, Fan, VentilationNetwork, VentJunction

    content = VentilationNetwork(
        junctions=[
            VentJunction(id=f"V{i}", position=p)
            for i, p in enumerate([[0, 0, 0], [80, 0, 0], [40, 0, 0], [40, 28, 0]])
        ],
        airways=[
            Airway(
                id="A1",
                from_junction="V0",
                to_junction="V2",
                length_m=40,
                area_m2=16,
                perimeter_m=16,
                friction_factor_kg_m3=0.012,
                provenance=SYN,
            ),
            Airway(
                id="A2",
                from_junction="V2",
                to_junction="V1",
                length_m=40,
                area_m2=16,
                perimeter_m=16,
                friction_factor_kg_m3=0.012,
                provenance=SYN,
            ),
            Airway(
                id="A3",
                from_junction="V2",
                to_junction="V3",
                length_m=28,
                area_m2=16,
                perimeter_m=16,
                friction_factor_kg_m3=0.012,
                provenance=SYN,
            ),
        ],
        fans=[Fan(id="F1", airway_id="A1", curve=[(0, 1200), (40, 1000), (80, 500)])],
    )
    return DomainModelEnvelope(
        discipline="ventilation", name="Mine section ventilation (synthetic)", content=content, provenance=SYN
    )


# ---------------------------------------------------------------- geomechanics (FLAC3D / 3DEC exchange)
def excavation_continuum_model() -> DomainModelEnvelope:
    from parallel_contracts.domain.geomechanics import (
        ContinuumModel,
        ExcavationStage,
        GeoMaterial,
        InSituStress,
        MonitoringPoint,
    )
    from parallel_contracts.geometry import BoxGeom

    mat = GeoMaterial(
        id="clay",
        name="Stiff clay (synthetic)",
        density_kg_m3=1900,
        youngs_modulus_pa=40e6,
        poisson_ratio=0.3,
        cohesion_pa=50e3,
        friction_deg=25,
        provenance=SYN,
    )
    content = ContinuumModel(
        extent=BoxGeom(center=[0, 0, -15], size=[60, 40, 30]),
        zone_size_m=1.0,
        materials=[mat],
        default_material_id="clay",
        stress=InSituStress(sxx_pa=-150e3, syy_pa=-150e3, szz_pa=-300e3, provenance=SYN),
        stages=[
            ExcavationStage(
                id="stage1", name="Excavate to -3 m", region=BoxGeom(center=[0, 0, -1.5], size=[20, 10, 3])
            ),
            ExcavationStage(
                id="stage2", name="Excavate to -6 m", region=BoxGeom(center=[0, 0, -4.5], size=[20, 10, 3])
            ),
        ],
        monitoring=[
            MonitoringPoint(id="crest", position=[11, 0, 0]),
            MonitoringPoint(id="base", position=[0, 0, -6]),
        ],
    )
    return DomainModelEnvelope(
        discipline="geomechanics", name="Basement excavation (synthetic)", content=content, provenance=SYN
    )


# ---------------------------------------------------------------- collision (Virtual CRASH / PC-Crash exchange)
def collision_case_model() -> DomainModelEnvelope:
    from parallel_contracts.domain.collision import (
        CollisionCaseModel,
        CollisionVehicle,
        EvidenceTarget,
        SurveyPoint,
    )
    from parallel_contracts.geometry import LineStringGeom

    content = CollisionCaseModel(
        friction_low=0.65,
        friction_high=0.8,
        friction_provenance=Provenance(
            kind=ProvenanceKind.assumption, note="Dry asphalt range (assumption, synthetic case)"
        ),
        road_edges=[
            LineStringGeom(coordinates=[[-50, -3.5], [50, -3.5]]),
            LineStringGeom(coordinates=[[-50, 3.5], [50, 3.5]]),
        ],
        survey_points=[
            SurveyPoint(id="sp1", label="Kerb mark A", position=[-10, -3.5, 0]),
            SurveyPoint(id="sp2", label="Kerb mark B", position=[10, -3.5, 0]),
        ],
        vehicles=[
            CollisionVehicle(
                id="V1", label="Vehicle 1 (training)", mass_kg=1350, length_m=4.3, width_m=1.8, provenance=SYN
            ),
            CollisionVehicle(
                id="V2",
                label="Vehicle 2 (training)",
                mass_kg=1600,
                length_m=4.6,
                width_m=1.85,
                provenance=SYN,
            ),
        ],
        targets=[
            EvidenceTarget(
                id="rest_V1",
                observation_id="obs_rest_v1",
                vehicle_id="V1",
                kind="rest_position",
                position=[12.0, 1.0],
                heading_deg=20.0,
                position_uncertainty_m=0.5,
            ),
            EvidenceTarget(
                id="rest_V2",
                observation_id="obs_rest_v2",
                vehicle_id="V2",
                kind="rest_position",
                position=[6.0, -2.0],
                heading_deg=100.0,
                position_uncertainty_m=0.5,
            ),
        ],
    )
    return DomainModelEnvelope(
        discipline="collision", name="Training collision case (synthetic)", content=content, provenance=SYN
    )
