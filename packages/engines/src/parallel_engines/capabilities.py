"""Capability records for every engine in Parallel's scope (spec sections 15.2 and 16).

``tested_versions[*].status`` is only ``verified`` where an automated reference
workflow executes the real engine (evidence = test id). Availability on a given
worker is never static: it comes from ``check_installation()``.
"""

from __future__ import annotations

from parallel_contracts.engines import EngineCapability, ExecutionMode, LicenceInfo, TestedVersion

M, C, F = ExecutionMode.managed, ExecutionMode.connected, ExecutionMode.file_exchange
VERIFIED_ON = "2026-10-22"
LINUX_CI = "Linux x86_64 (Ubuntu 24.04, Python 3.13, CPU only)"


def _verified(version: str, evidence: str) -> TestedVersion:
    return TestedVersion(
        version=version, platform=LINUX_CI, verified_on=VERIFIED_ON, evidence=evidence, status="verified"
    )


def _awaiting(version: str, platform: str, note: str) -> TestedVersion:
    return TestedVersion(version=version, platform=platform, evidence=note, status="awaiting_environment")


JUPEDSIM = EngineCapability(
    engine_id="jupedsim",
    name="JuPedSim",
    vendor="JuPedSim project (Forschungszentrum Jülich)",
    role="Pedestrian movement and evacuation.",
    disciplines=["pedestrian"],
    dimensions=["2D planar (single level)"],
    model_formats_in=[
        "Parallel pedestrian_geometry schema (walkable polygon, excluded areas, exits, start groups)"
    ],
    model_formats_out=["agents.csv exit records", "trajectories.parquet", "population.json"],
    input_units={"length": "m", "speed": "m/s", "time": "s"},
    output_units={"position": "m", "time": "s"},
    runtime_requirements=["Python runtime with jupedsim==1.4.2 (isolated venv)"],
    operating_systems=["Linux", "macOS", "Windows"],
    licence=LicenceInfo(kind="open_source", name="LGPL-3.0"),
    execution_modes=[M],
    cancellation="Runner process group terminated; partial outputs discarded.",
    resource_estimate="Proportional to agents x simulated steps (calibrated on reference workflow).",
    result_types=["trajectories", "exit records", "evacuation metrics"],
    limitations=[
        "One approximately level section per model; multi-level transfers are not modelled.",
        "Uses the CollisionFreeSpeedModel only (pinned parameters); other JuPedSim models are not exposed.",
        "Disconnected walkable components are simulated as separate models with reachability accounting.",
        "Before their movement start (alarm + pre-movement), people stand still at their start positions.",
        "Timed closures and evolving hazards are disabled until implemented and tested.",
        "Nearest-exit assignment uses JuPedSim routing path length at t = 0; no dynamic re-routing.",
    ],
    tested_versions=[_verified("1.4.2", "packages/engines/tests/test_engine_jupedsim.py")],
    pinned_version="1.4.2",
    official_docs=["https://www.jupedsim.org/stable/"],
    adapter_version="0.1.0",
)

ISAACSIM = EngineCapability(
    engine_id="isaacsim",
    name="NVIDIA Isaac Sim",
    vendor="NVIDIA",
    role="Robots, equipment movement, rigid-body interaction and configured sensors.",
    disciplines=["rigid_body"],
    dimensions=["3D"],
    model_formats_in=["Parallel rigid_body schema", "collision meshes (OBJ/USD) from reconstruction"],
    model_formats_out=["states.csv (body poses and velocities)", "contacts"],
    input_units={"length": "m", "mass": "kg", "time": "s"},
    output_units={"position": "m", "velocity": "m/s"},
    runtime_requirements=[
        "NVIDIA RTX GPU and supported driver",
        "Isaac Sim standalone Python (python.sh)",
        "Dedicated GPU worker image",
    ],
    operating_systems=["Linux", "Windows"],
    licence=LicenceInfo(
        kind="free_proprietary",
        name="NVIDIA Isaac Sim licence terms of the selected release",
        note="Verify the selected release's licence before deployment.",
    ),
    execution_modes=[M, C],
    cancellation="Standalone process group terminated on the GPU worker.",
    resource_estimate="GPU worker; duration x physics rate.",
    result_types=["body states", "contacts", "configured sensor outputs"],
    limitations=[
        "Requires an RTX GPU; unavailable on CPU-only workers.",
        "General scene imports are refused until the reference box-drop scenario passes on the installation.",
        "Photorealistic splats carry no collision geometry or physical properties; collision meshes are separate.",
    ],
    tested_versions=[
        _awaiting(
            "selected Isaac Sim 5.x release",
            "Linux x86_64 + NVIDIA RTX GPU",
            "Reference scenario packages/engines/src/parallel_engines/runners/isaacsim_runner.py "
            "awaiting a GPU worker",
        )
    ],
    pinned_version=None,
    official_docs=["https://docs.isaacsim.omniverse.nvidia.com/"],
    adapter_version="0.1.0",
)

SUMO = EngineCapability(
    engine_id="sumo",
    name="Eclipse SUMO",
    vendor="Eclipse Foundation / German Aerospace Center (DLR)",
    role="Road traffic and transport scenarios.",
    disciplines=["traffic"],
    dimensions=["1D lane network (microscopic)"],
    model_formats_in=[
        "Parallel road_network schema -> SUMO plain XML (nod/edg/tll) -> netconvert",
        "routes (flows)",
    ],
    model_formats_out=["tripinfo.xml", "statistics.xml", "summary.xml", "queue.xml", "fcd.xml"],
    input_units={"length": "m", "speed": "m/s", "time": "s"},
    output_units={"time": "s", "length": "m"},
    runtime_requirements=["Python runtime with eclipse-sumo==1.28.0 and traci==1.28.0 (isolated venv)"],
    operating_systems=["Linux", "macOS", "Windows"],
    licence=LicenceInfo(kind="open_source", name="EPL-2.0 OR GPL-2.0-or-later"),
    execution_modes=[M],
    cancellation="TraCI-controlled run terminated (process group).",
    resource_estimate="Proportional to vehicles x simulated steps.",
    result_types=[
        "travel times",
        "delay (timeLoss)",
        "queues",
        "throughput",
        "trip accounting",
        "vehicle trajectories",
    ],
    limitations=[
        "Demand is an explicit assumption unless imported from a calibrated source.",
        "Vehicles whose route crosses a closed edge are rerouted at insertion; unroutable trips are counted as unfinished.",
        "Teleports are disabled (time-to-teleport -1) so gridlock appears as unfinished trips rather than artefacts.",
        "Not a collision reconstruction tool.",
    ],
    tested_versions=[_verified("1.28.0", "packages/engines/tests/test_engine_sumo.py")],
    pinned_version="1.28.0",
    official_docs=["https://sumo.dlr.de/docs/"],
    adapter_version="0.1.0",
)

FDS = EngineCapability(
    engine_id="fds",
    name="NIST Fire Dynamics Simulator",
    vendor="National Institute of Standards and Technology",
    role="Fire-driven smoke and heat transport.",
    disciplines=["fire"],
    dimensions=["3D (LES)"],
    model_formats_in=["Parallel fire schema -> FDS namelist input (.fds)"],
    model_formats_out=["CHID_hrr.csv", "CHID_devc.csv", "Smokeview files"],
    input_units={"length": "m", "time": "s", "hrrpua": "kW/m2"},
    output_units={"temperature": "degC", "hrr": "kW"},
    runtime_requirements=["FDS 6.10 executable (fds) on the worker, optionally MPI for multi-mesh"],
    operating_systems=["Linux", "macOS", "Windows"],
    licence=LicenceInfo(kind="public_domain", name="Public domain (U.S. Government work)"),
    execution_modes=[M, F],
    cancellation="Process group terminated; FDS also honours a CHID.stop file (not used).",
    resource_estimate="Cells x simulated time; refuse meshes above configured cell limit.",
    result_types=["device time series", "heat release rate"],
    limitations=[
        "Fire and pedestrian models are separate; FDS+Evac (discontinued) is not used.",
        "Fire-to-evacuation coupling is not implemented; it requires a separately specified and tested exchange.",
        "Material fire properties must be supplied with provenance, never inferred from appearance.",
    ],
    tested_versions=[
        _awaiting(
            "6.10.1",
            "Linux x86_64",
            "FDS binary not installable in the build environment (release downloads blocked)",
        )
    ],
    pinned_version="6.10.1",
    official_docs=["https://pages.nist.gov/fds-smv/"],
    adapter_version="0.1.0",
)

SWMM = EngineCapability(
    engine_id="swmm",
    name="EPA SWMM",
    vendor="U.S. Environmental Protection Agency",
    role="Urban runoff and drainage networks.",
    disciplines=["drainage"],
    dimensions=["1D network + lumped catchments"],
    model_formats_in=["Parallel drainage_network schema -> SWMM 5.2 .inp (CMS units)"],
    model_formats_out=["model.out (binary)", "model.rpt"],
    input_units={"flow": "m3/s", "length": "m", "rain": "mm/h", "area": "ha"},
    output_units={"flow": "m3/s", "depth": "m", "volume": "m3"},
    runtime_requirements=["Python runtime with swmm-toolkit==0.17.0 and pyswmm==2.2.0 (isolated venv)"],
    operating_systems=["Linux", "macOS", "Windows"],
    licence=LicenceInfo(kind="public_domain", name="Public domain (EPA SWMM); swmm-toolkit/pyswmm BSD-2/CC0"),
    execution_modes=[M],
    cancellation="Step loop stops and the runner exits; partial outputs discarded.",
    resource_estimate="Routing steps x network size.",
    result_types=["runoff", "node depths", "flooding", "surcharge", "conduit fill", "continuity errors"],
    limitations=[
        "Node flooding is a network quantity; SWMM results alone do not compute surface inundation extents.",
        "Blockage is represented explicitly as FILLED_CIRCULAR sediment depth (depth fraction, area fraction reported).",
        "Storage additions replace a junction by a constant-area storage unit at the same invert.",
    ],
    tested_versions=[
        _verified("5.2.4 via swmm-toolkit 0.17.0", "packages/engines/tests/test_engine_swmm.py")
    ],
    pinned_version="swmm-toolkit 0.17.0",
    official_docs=[
        "https://www.epa.gov/water-research/storm-water-management-model-swmm",
        "https://pyswmm.readthedocs.io/",
    ],
    adapter_version="0.1.0",
)

EPANET = EngineCapability(
    engine_id="epanet",
    name="EPA EPANET",
    vendor="U.S. Environmental Protection Agency",
    role="Pressurised water networks.",
    disciplines=["water_supply"],
    dimensions=["1D network (extended period)"],
    model_formats_in=["Parallel water_supply schema -> EPANET .inp via WNTR"],
    model_formats_out=["EPANET binary results", "model.rpt"],
    input_units={"flow": "L/s", "length": "m", "diameter": "mm"},
    output_units={"pressure": "m", "flow": "m3/s"},
    runtime_requirements=["Python runtime with wntr==1.5.0 (bundles the EPANET 2.2 toolkit)"],
    operating_systems=["Linux", "macOS", "Windows"],
    licence=LicenceInfo(kind="public_domain", name="EPANET public domain; WNTR Revised BSD"),
    execution_modes=[M],
    cancellation="Runner process group terminated.",
    resource_estimate="Hydraulic steps x network size.",
    result_types=["pressure", "flow", "velocity", "tank levels"],
    limitations=["Hydraulics only (no water-quality analysis exposed).", "Demand-driven analysis."],
    tested_versions=[_verified("EPANET 2.2 via wntr 1.5.0", "packages/engines/tests/test_engine_epanet.py")],
    pinned_version="wntr 1.5.0",
    official_docs=["https://www.epa.gov/water-research/epanet", "https://usepa.github.io/WNTR/"],
    adapter_version="0.1.0",
)

HECRAS = EngineCapability(
    engine_id="hecras",
    name="HEC-RAS",
    vendor="U.S. Army Corps of Engineers, Hydrologic Engineering Center",
    role="River and surface-flow hydraulic modelling.",
    disciplines=["surface_hydraulics"],
    dimensions=["1D/2D"],
    model_formats_in=["Exchange package: terrain GeoTIFF, perimeter/boundary GeoJSON, hydrograph CSV"],
    model_formats_out=["Max depth / velocity GeoTIFF exports (RAS Mapper)"],
    input_units={"length": "m", "flow": "m3/s"},
    output_units={"depth": "m", "velocity": "m/s"},
    runtime_requirements=[
        "HEC-RAS 6.6 (Windows authoring + RAS Mapper)",
        "Optional: HEC-RAS Linux compute engines for prepared plans",
    ],
    operating_systems=["Windows (authoring)", "Linux (compute engines only)"],
    licence=LicenceInfo(kind="public_domain", name="Free U.S. Government software (USACE HEC terms)"),
    execution_modes=[F, M],
    cancellation="File exchange: operator abandons; managed: process group terminated.",
    resource_estimate="Operator workflow; compute time depends on mesh and plan.",
    result_types=["max depth raster", "max velocity raster"],
    limitations=[
        "Authoring and RAS Mapper exports are Windows workflows; Linux compute engines run only prepared plans.",
        "Imported results must state terrain, mesh, boundaries, time reference and vertical datum.",
    ],
    tested_versions=[
        _awaiting(
            "6.6",
            "Windows 10/11 operator workstation",
            "Exchange package and raster import implemented; awaiting an operator execution record",
        )
    ],
    pinned_version="6.6",
    official_docs=["https://www.hec.usace.army.mil/software/hec-ras/"],
    adapter_version="0.1.0",
)

SIMPY = EngineCapability(
    engine_id="simpy",
    name="SimPy",
    vendor="SimPy project",
    role="Discrete-event operations.",
    disciplines=["operations"],
    dimensions=["discrete-event (non-spatial)"],
    model_formats_in=["Parallel operations schema (resources, flows, steps, distributions)"],
    model_formats_out=["events.csv request log", "entities.csv"],
    input_units={"time": "s"},
    output_units={"time": "s"},
    runtime_requirements=["Python runtime with simpy==4.1.2 (isolated venv)"],
    operating_systems=["Linux", "macOS", "Windows"],
    licence=LicenceInfo(kind="open_source", name="MIT"),
    execution_modes=[M],
    cancellation="Runner process group terminated.",
    resource_estimate="Entities x steps x replications.",
    result_types=["waiting times", "utilisation", "queue lengths", "throughput", "cycle times"],
    limitations=[
        "Discrete-event only: no continuous fluid calculations or spatial collision modelling.",
        "Stochastic inputs are pre-sampled from the run seed (common random numbers across scenarios).",
    ],
    tested_versions=[_verified("4.1.2", "packages/engines/tests/test_engine_simpy.py")],
    pinned_version="4.1.2",
    official_docs=["https://simpy.readthedocs.io/"],
    adapter_version="0.1.0",
)

VENTSIM = EngineCapability(
    engine_id="ventsim",
    name="Ventsim",
    vendor="Howden (Ventsim)",
    role="Mine ventilation modelling.",
    disciplines=["ventilation"],
    dimensions=["1D airway network"],
    model_formats_in=["Exchange package: airway/junction/fan CSV tables in Parallel's neutral format"],
    model_formats_out=["Operator-exported airway results table (CSV) mapped to Parallel's template"],
    input_units={"length": "m", "area": "m2", "resistance": "Ns2/m8"},
    output_units={"airflow": "m3/s", "pressure": "Pa"},
    runtime_requirements=["Licensed Ventsim Design installation (Windows)", "Operator"],
    operating_systems=["Windows"],
    licence=LicenceInfo(
        kind="commercial",
        name="Ventsim commercial licence",
        requires_key=True,
        note="Record the product edition and licence holder in the integration settings.",
    ),
    execution_modes=[F],
    cancellation="Operator abandons the exchange.",
    resource_estimate="Operator workflow.",
    result_types=["airflow", "pressure drop"],
    limitations=[
        "No public execution API is assumed; integration is a documented file exchange.",
        "The neutral exchange tables have not been verified against a licensed Ventsim installation.",
        "Ventilation properties are kept separate from captured tunnel appearance.",
    ],
    tested_versions=[
        TestedVersion(
            version="unspecified",
            platform="Windows",
            status="not_verified",
            evidence="Requires a licensed installation and vendor documentation",
        )
    ],
    pinned_version=None,
    official_docs=["https://ventsim.com/"],
    adapter_version="0.1.0",
)

OPENFOAM = EngineCapability(
    engine_id="openfoam",
    name="OpenFOAM",
    vendor="OpenCFD Ltd (ESI) — openfoam.com distribution",
    role="Configured local fluid-flow studies (approved case templates only).",
    disciplines=["cfd"],
    dimensions=["2D (one cell thick, empty front/back)"],
    model_formats_in=["Parallel cfd_wind2d schema -> blockMesh case (template wind2d_obstacles)"],
    model_formats_out=["OpenFOAM case directory", "log.blockMesh", "log.checkMesh", "log.simpleFoam"],
    input_units={"length": "m", "speed": "m/s"},
    output_units={"speed": "m/s"},
    runtime_requirements=[
        "OpenFOAM v1912 (Ubuntu 24.04 package 'openfoam' 1912.200626) with WM_PROJECT_DIR set"
    ],
    operating_systems=["Linux"],
    licence=LicenceInfo(kind="open_source", name="GPL-3.0"),
    execution_modes=[M],
    cancellation="Process group terminated between or during utilities.",
    resource_estimate="Cells x iterations; refuse meshes above max_cells.",
    result_types=["velocity field", "exceedance area", "residuals", "mesh quality"],
    limitations=[
        "Only the approved template 'wind2d_obstacles' (steady simpleFoam, k-epsilon, plan-view 2D) is executed.",
        "Plan-view 2D flow ignores vertical effects; results describe the configured template only.",
        "A general OpenFOAM installation is not a usable study workflow; arbitrary cases are not accepted.",
    ],
    tested_versions=[_verified("v1912", "packages/engines/tests/test_engine_openfoam.py")],
    pinned_version="v1912",
    official_docs=["https://www.openfoam.com/documentation/"],
    adapter_version="0.1.0",
)

FLAC3D = EngineCapability(
    engine_id="flac3d",
    name="FLAC3D",
    vendor="Itasca Consulting Group",
    role="Continuum geomechanical analysis.",
    disciplines=["geomechanics"],
    dimensions=["3D continuum"],
    model_formats_in=["Exchange package: neutral model JSON + material/stage tables"],
    model_formats_out=["Operator-exported monitoring table (CSV) mapped to Parallel's template"],
    input_units={"length": "m", "stress": "Pa", "density": "kg/m3"},
    output_units={"displacement": "m", "stress": "Pa"},
    runtime_requirements=["Licensed FLAC3D installation (Windows)", "Geotechnical specialist"],
    operating_systems=["Windows"],
    licence=LicenceInfo(kind="commercial", name="Itasca FLAC3D licence", requires_key=True),
    execution_modes=[F],
    cancellation="Operator abandons the exchange.",
    resource_estimate="Operator workflow.",
    result_types=["displacement", "stress", "model-specific stability outputs"],
    limitations=[
        "Material properties, in-situ stress and loading require specialist inputs with provenance.",
        "No command script is generated: command syntax has not been verified against a licensed installation.",
    ],
    tested_versions=[
        TestedVersion(
            version="unspecified",
            platform="Windows",
            status="not_verified",
            evidence="Requires a licensed installation",
        )
    ],
    pinned_version=None,
    official_docs=["https://docs.itascacg.com/"],
    adapter_version="0.1.0",
)

THREEDEC = EngineCapability(
    engine_id="3dec",
    name="3DEC",
    vendor="Itasca Consulting Group",
    role="Jointed rock and discrete-block analysis.",
    disciplines=["geomechanics"],
    dimensions=["3D discrete block"],
    model_formats_in=["Exchange package: neutral block model JSON + joint set table"],
    model_formats_out=["Operator-exported monitoring table (CSV) mapped to Parallel's template"],
    input_units={"length": "m", "stress": "Pa", "stiffness": "Pa/m"},
    output_units={"displacement": "m"},
    runtime_requirements=["Licensed 3DEC installation (Windows)", "Geotechnical specialist"],
    operating_systems=["Windows"],
    licence=LicenceInfo(kind="commercial", name="Itasca 3DEC licence", requires_key=True),
    execution_modes=[F],
    cancellation="Operator abandons the exchange.",
    resource_estimate="Operator workflow.",
    result_types=["block movement", "joint response", "stress/deformation"],
    limitations=[
        "Discontinuity data (orientation, spacing, strength, stiffness) must come from site investigation.",
        "No command script is generated: command syntax has not been verified against a licensed installation.",
    ],
    tested_versions=[
        TestedVersion(
            version="unspecified",
            platform="Windows",
            status="not_verified",
            evidence="Requires a licensed installation",
        )
    ],
    pinned_version=None,
    official_docs=["https://docs.itascacg.com/"],
    adapter_version="0.1.0",
)

ENERGYPLUS = EngineCapability(
    engine_id="energyplus",
    name="EnergyPlus (with OpenStudio)",
    vendor="U.S. DOE / NREL",
    role="Building energy and thermal analysis.",
    disciplines=["thermal"],
    dimensions=["multi-zone heat balance"],
    model_formats_in=["Parallel thermal schema -> OpenStudio model (.osm) -> EnergyPlus IDF"],
    model_formats_out=["eplusout.sql", "eplustbl.csv", "eplusout.err"],
    input_units={"length": "m", "u_value": "W/m2K", "temperature": "degC"},
    output_units={"power": "W", "energy": "kWh"},
    runtime_requirements=[
        "OpenStudio SDK 3.11.0 Python bindings (isolated venv) for model preparation",
        "EnergyPlus 25.2.0 executable for simulation",
    ],
    operating_systems=["Linux", "macOS", "Windows"],
    licence=LicenceInfo(kind="open_source", name="EnergyPlus licence (BSD-3-like); OpenStudio BSD-3"),
    execution_modes=[M, F],
    cancellation="Process group terminated.",
    resource_estimate="Zones x timesteps x run period.",
    result_types=["loads", "energy", "temperatures"],
    limitations=[
        "Geometry, constructions, schedules, systems and weather are explicit inputs, never inferred from appearance.",
        "Ideal loads air systems only (no detailed HVAC).",
        "OpenStudio model preparation (IDF generation) is verified; EnergyPlus execution awaits an installation.",
    ],
    tested_versions=[
        _verified(
            "OpenStudio 3.11.0 (model preparation only)", "packages/engines/tests/test_engine_energyplus.py"
        ),
        _awaiting(
            "EnergyPlus 25.2.0", "Linux x86_64", "EnergyPlus binary not installable in the build environment"
        ),
    ],
    pinned_version="25.2.0",
    official_docs=[
        "https://energyplus.net/documentation",
        "https://openstudio-sdk-documentation.s3.amazonaws.com/index.html",
    ],
    adapter_version="0.1.0",
    companion_tools=["OpenStudio SDK 3.11.0"],
)

VIRTUALCRASH = EngineCapability(
    engine_id="virtualcrash",
    name="Virtual CRASH",
    vendor="Virtual CRASH",
    role="Specialist collision analysis and reconstruction workflows.",
    disciplines=["collision"],
    dimensions=["3D vehicle dynamics"],
    model_formats_in=[
        "Exchange package: survey points CSV, road edges DXF (R12), vehicle and evidence tables"
    ],
    model_formats_out=[
        "Text export of simulation data (CSV), mapped by the operator to time/x/y/heading/speed"
    ],
    input_units={"length": "m", "mass": "kg"},
    output_units={"position": "m", "speed": "m/s", "time": "s"},
    runtime_requirements=["Licensed Virtual CRASH installation", "Qualified reconstructionist"],
    operating_systems=["Windows"],
    licence=LicenceInfo(kind="commercial", name="Virtual CRASH licence", requires_key=True),
    execution_modes=[F],
    cancellation="Operator abandons the exchange.",
    resource_estimate="Operator workflow.",
    result_types=["exported trajectories", "evidence residuals (computed by Parallel)"],
    limitations=[
        "Virtual CRASH documents text export of simulation data; no public server-side solver API is assumed.",
        "Column mapping of exported text is confirmed by the operator for each import.",
        "Parallel computes evidence residuals only; it does not determine fault or legal admissibility.",
    ],
    tested_versions=[
        _awaiting(
            "unspecified",
            "Windows",
            "Trajectory import tested with a Parallel-authored CSV fixture, not a vendor export",
        )
    ],
    pinned_version=None,
    official_docs=["https://www.vcrashusa.com/"],
    adapter_version="0.1.0",
)

PCCRASH = EngineCapability(
    engine_id="pccrash",
    name="PC-Crash",
    vendor="DSD — Dr. Steffan Datentechnik",
    role="Specialist collision analysis and reconstruction workflows.",
    disciplines=["collision"],
    dimensions=["3D vehicle dynamics"],
    model_formats_in=[
        "Exchange package: survey points CSV, road edges DXF (R12), vehicle and evidence tables"
    ],
    model_formats_out=["To be specified after vendor documentation is obtained and tested"],
    input_units={"length": "m", "mass": "kg"},
    output_units={},
    runtime_requirements=["Licensed PC-Crash installation", "Vendor documentation for export formats"],
    operating_systems=["Windows"],
    licence=LicenceInfo(kind="commercial", name="PC-Crash licence", requires_key=True),
    execution_modes=[F],
    cancellation="Operator abandons the exchange.",
    resource_estimate="Operator workflow.",
    result_types=["supported trajectories, reports and analysis outputs (unspecified)"],
    limitations=[
        "Automated execution and result formats are not specified until vendor documentation is obtained and tested.",
        "Result import is disabled (integration incomplete); scene packages can be exported for the analyst.",
    ],
    tested_versions=[
        TestedVersion(
            version="unspecified",
            platform="Windows",
            status="not_verified",
            evidence="Vendor documentation not yet obtained",
        )
    ],
    pinned_version=None,
    official_docs=["https://www.dsd.at/"],
    adapter_version="0.1.0",
)

ALL_CAPABILITIES: list[EngineCapability] = [
    JUPEDSIM,
    ISAACSIM,
    SUMO,
    FDS,
    SWMM,
    EPANET,
    HECRAS,
    SIMPY,
    VENTSIM,
    OPENFOAM,
    FLAC3D,
    THREEDEC,
    ENERGYPLUS,
    VIRTUALCRASH,
    PCCRASH,
]
