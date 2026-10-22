"""Registry of documented metric definitions.

Every metric that Parallel displays, compares or reports is defined here with a
stable identifier. Adding an undocumented score is not possible: normalisers
may only emit metric ids present in this registry (enforced in tests).
"""

from __future__ import annotations

from parallel_contracts.metrics import MetricDefinition

_D: list[MetricDefinition] = []


def _m(**kw) -> None:
    _D.append(MetricDefinition(**kw))


# ---------------------------------------------------------------- evacuation (JuPedSim)
_EVAC_SRC = ["population.json (materialised inputs)", "agents.csv (runner exit records)"]
_m(
    id="evac.initial_population",
    name="Initial population",
    unit="count",
    discipline="pedestrian",
    definition="Number of people in the initial population of the scenario.",
    calculation="Sum of start-group counts after scenario overrides.",
    source_outputs=_EVAC_SRC,
    extent="Whole model",
    time_interval="t = 0",
    missing_value_rule="Never missing.",
)
_m(
    id="evac.evacuated",
    name="Evacuated",
    unit="count",
    discipline="pedestrian",
    definition="People who reached an open exit or refuge within the simulation horizon.",
    calculation="Count of agents removed by JuPedSim at an exit stage with exit time <= horizon.",
    source_outputs=_EVAC_SRC,
    extent="Whole model",
    time_interval="0 .. horizon",
    missing_value_rule="Never missing.",
)
_m(
    id="evac.unreachable",
    name="Initially unable to reach a destination",
    unit="count",
    discipline="pedestrian",
    definition="People whose start position has no open, permitted destination in the same connected walkable component.",
    calculation="Connectivity analysis of the walkable area after closures; these people are not simulated.",
    source_outputs=["population.json"],
    extent="Whole model",
    time_interval="t = 0",
    missing_value_rule="Never missing.",
)
_m(
    id="evac.remaining",
    name="Remaining at horizon",
    unit="count",
    discipline="pedestrian",
    definition="Reachable people still inside the model at the simulation horizon.",
    calculation="Agents still present in the JuPedSim simulation when the horizon is reached.",
    source_outputs=_EVAC_SRC,
    extent="Whole model",
    time_interval="at horizon",
    missing_value_rule="Never missing.",
)
for pct in (50, 95, 100):
    _m(
        id=f"evac.t{pct}",
        name=f"T{pct} evacuation time",
        unit="s",
        discipline="pedestrian",
        definition=f"Time from the alarm (t = 0) until at least {pct}% of the initial population has evacuated.",
        calculation=f"Smallest exit time t such that evacuated(t) >= ceil({pct}% x initial population).",
        source_outputs=_EVAC_SRC,
        extent="Whole model (initial population, including unreachable people)",
        time_interval="0 .. horizon",
        missing_value_rule="Unattained (null) when the threshold is not reached within the horizon; "
        "never replaced by the last exit time.",
    )
_m(
    id="evac.mean_travel_time",
    name="Mean travel time",
    unit="s",
    discipline="pedestrian",
    definition="Mean, over evacuated people, of exit time minus movement start time (alarm + pre-movement).",
    calculation="mean(exit_time_i - start_time_i) for evacuated agents.",
    source_outputs=_EVAC_SRC,
    extent="Evacuated people",
    time_interval="0 .. horizon",
    missing_value_rule="Unavailable when nobody evacuated.",
)
_m(
    id="evac.p95_travel_time",
    name="95th percentile travel time",
    unit="s",
    discipline="pedestrian",
    definition="95th percentile of travel time over evacuated people.",
    calculation="Linear-interpolated percentile (numpy) of exit_time_i - start_time_i.",
    source_outputs=_EVAC_SRC,
    extent="Evacuated people",
    time_interval="0 .. horizon",
    missing_value_rule="Unavailable when nobody evacuated.",
)
_m(
    id="evac.exit_usage",
    name="Exit usage",
    unit="count",
    discipline="pedestrian",
    per_entity=True,
    definition="Number of people who evacuated through each exit or refuge.",
    calculation="Count of evacuated agents by exit id.",
    source_outputs=_EVAC_SRC,
    extent="Per exit",
    time_interval="0 .. horizon",
    missing_value_rule="Zero for open exits nobody used; not reported for closed exits.",
)
_m(
    id="evac.peak_exit_density",
    name="Peak density near exit",
    unit="1/m2",
    discipline="pedestrian",
    per_entity=True,
    definition="Maximum over recorded frames of the number of agents within 2 m of the exit polygon divided by "
    "the walkable area within 2 m of the exit.",
    calculation="Per trajectory frame: count agents with distance(position, exit polygon) <= 2 m; divide by "
    "area(buffer(exit, 2 m) ∩ walkable area); take the maximum over frames.",
    source_outputs=["trajectories.parquet"],
    extent="2 m band around each open exit",
    time_interval="Recorded frames (every n simulation steps)",
    missing_value_rule="Unavailable when no trajectory frames were recorded.",
)
_m(
    id="evac.slow_movement_fraction",
    name="Slow-movement fraction",
    unit="1",
    discipline="pedestrian",
    definition="Share of moving-phase trajectory samples in which an agent's speed is below 30% of its desired speed "
    "(a congestion indicator, not a safety score).",
    calculation="Speeds from successive recorded frames; samples after each agent's movement start; "
    "count(speed < 0.3 x desired speed) / count(samples).",
    source_outputs=["trajectories.parquet", "population.json"],
    extent="All simulated agents",
    time_interval="Movement start .. exit or horizon",
    missing_value_rule="Unavailable when fewer than two frames were recorded.",
)

# ---------------------------------------------------------------- traffic (SUMO)
_TR = ["tripinfo.xml", "statistics.xml", "summary.xml"]
_m(
    id="traffic.scheduled_trips",
    name="Scheduled trips",
    unit="veh",
    discipline="traffic",
    definition="Vehicles scheduled by the demand definition (after demand scaling).",
    calculation="Sum of flow 'number' attributes written to the SUMO route file.",
    source_outputs=["routes.rou.xml"],
    extent="Network",
    time_interval="Demand period",
    missing_value_rule="Never missing.",
)
_m(
    id="traffic.completed_trips",
    name="Completed trips",
    unit="veh",
    discipline="traffic",
    definition="Vehicles that arrived at their destination before the simulation end.",
    calculation="Number of tripinfo records.",
    source_outputs=_TR,
    extent="Network",
    time_interval="0 .. end",
    missing_value_rule="Never missing.",
)
_m(
    id="traffic.unfinished_trips",
    name="Unfinished trips",
    unit="veh",
    discipline="traffic",
    definition="Scheduled vehicles that did not complete their trip: still driving at the end, never inserted, "
    "or discarded because no route existed.",
    calculation="scheduled - completed (cross-checked against running + waiting + discarded in statistics.xml).",
    source_outputs=_TR,
    extent="Network",
    time_interval="at end",
    missing_value_rule="Never missing.",
)
_m(
    id="traffic.not_inserted",
    name="Not inserted",
    unit="veh",
    discipline="traffic",
    definition="Scheduled vehicles never inserted into the network (waiting for insertion or discarded).",
    calculation="statistics.xml vehicles: waiting + (loaded - inserted - waiting) i.e. discarded.",
    source_outputs=["statistics.xml"],
    extent="Network",
    time_interval="at end",
    missing_value_rule="Never missing.",
)
_m(
    id="traffic.completion_ratio",
    name="Completion ratio",
    unit="1",
    discipline="traffic",
    definition="Completed trips divided by scheduled trips.",
    calculation="completed / scheduled",
    source_outputs=_TR,
    extent="Network",
    time_interval="0 .. end",
    missing_value_rule="Unavailable when no trips were scheduled.",
)
_m(
    id="traffic.mean_travel_time",
    name="Mean travel time (completed trips)",
    unit="s",
    discipline="traffic",
    definition="Mean duration of completed trips departing after the warm-up period.",
    calculation="mean(tripinfo.duration) for trips with depart >= warm-up. Must be read with the completion ratio.",
    source_outputs=["tripinfo.xml"],
    extent="Completed trips",
    time_interval="warm-up .. end",
    missing_value_rule="Unavailable when no trips completed after warm-up.",
)
_m(
    id="traffic.p95_travel_time",
    name="95th percentile travel time",
    unit="s",
    discipline="traffic",
    definition="95th percentile duration of completed trips departing after warm-up.",
    calculation="numpy percentile of tripinfo.duration.",
    source_outputs=["tripinfo.xml"],
    extent="Completed trips",
    time_interval="warm-up .. end",
    missing_value_rule="Unavailable when no trips completed after warm-up.",
)
_m(
    id="traffic.mean_time_loss",
    name="Mean delay (time loss)",
    unit="s",
    discipline="traffic",
    definition="SUMO timeLoss: time lost due to driving below the ideal speed (includes waiting), averaged over "
    "completed trips departing after warm-up.",
    calculation="mean(tripinfo.timeLoss).",
    source_outputs=["tripinfo.xml"],
    extent="Completed trips",
    time_interval="warm-up .. end",
    missing_value_rule="Unavailable when no trips completed after warm-up.",
)
_m(
    id="traffic.throughput",
    name="Throughput",
    unit="veh/h",
    discipline="traffic",
    definition="Completed trips per hour over the measured interval.",
    calculation="count(arrival in [warm-up, end]) / ((end - warm-up) / 3600).",
    source_outputs=["tripinfo.xml"],
    extent="Network",
    time_interval="warm-up .. end",
    missing_value_rule="Never missing.",
)
_m(
    id="traffic.max_queue_length",
    name="Maximum queue length",
    unit="m",
    discipline="traffic",
    per_entity=True,
    definition="Maximum queueing length reported on any lane of the edge.",
    calculation="max over time and lanes of queue-output 'queueing_length'.",
    source_outputs=["queue.xml"],
    extent="Per edge",
    time_interval="0 .. end",
    missing_value_rule="Zero when no queue was reported.",
)
_m(
    id="traffic.network_max_queue_length",
    name="Network maximum queue length",
    unit="m",
    discipline="traffic",
    definition="Largest queueing length on any lane.",
    calculation="max of traffic.max_queue_length.",
    source_outputs=["queue.xml"],
    extent="Network",
    time_interval="0 .. end",
    missing_value_rule="Zero when no queue was reported.",
)
_m(
    id="traffic.teleports",
    name="Teleports",
    unit="count",
    discipline="traffic",
    definition="Vehicles teleported by SUMO's jam resolution (a model artefact that invalidates delay figures).",
    calculation="statistics.xml teleports total.",
    source_outputs=["statistics.xml"],
    extent="Network",
    time_interval="0 .. end",
    missing_value_rule="Never missing.",
)

# ---------------------------------------------------------------- drainage (SWMM)
_SW = ["model.out (SWMM binary output)", "model.rpt"]
_m(
    id="drainage.total_rainfall_volume",
    name="Rainfall volume on catchments",
    unit="m3",
    discipline="drainage",
    definition="Rainfall depth times subcatchment area, summed over subcatchments.",
    calculation="SWMM runoff continuity 'Total Precipitation' (converted from hectare-metres).",
    source_outputs=["model.rpt"],
    extent="All subcatchments",
    time_interval="Simulation period",
    missing_value_rule="Unavailable without subcatchments.",
)
_m(
    id="drainage.total_runoff_volume",
    name="Runoff volume",
    unit="m3",
    discipline="drainage",
    definition="Surface runoff volume generated by all subcatchments.",
    calculation="Trapezoidal integral of system RUNOFF flow (m3/s) over the report time series.",
    source_outputs=_SW,
    extent="All subcatchments",
    time_interval="Simulation period",
    missing_value_rule="Unavailable without subcatchments.",
)
_m(
    id="drainage.total_flooding_volume",
    name="Flooding volume",
    unit="m3",
    discipline="drainage",
    definition="Volume of water lost from the network through node flooding (overflow above the rim).",
    calculation="Trapezoidal integral of system FLOODING flow (m3/s); cross-checked with the report's flow routing "
    "continuity 'Flooding Loss'.",
    source_outputs=_SW,
    extent="Network",
    time_interval="Simulation period",
    missing_value_rule="Never missing.",
)
_m(
    id="drainage.node_flooding_volume",
    name="Node flooding volume",
    unit="m3",
    discipline="drainage",
    per_entity=True,
    definition="Flooding volume at each node.",
    calculation="Trapezoidal integral of node FLOODING_LOSSES (m3/s).",
    source_outputs=_SW,
    extent="Per node",
    time_interval="Simulation period",
    missing_value_rule="Zero for nodes that did not flood.",
)
_m(
    id="drainage.node_flood_duration",
    name="Node flooding duration",
    unit="h",
    discipline="drainage",
    per_entity=True,
    definition="Time a node spent flooding.",
    calculation="Sum of report intervals with node FLOODING_LOSSES > 1e-6 m3/s.",
    source_outputs=_SW,
    extent="Per node",
    time_interval="Simulation period",
    missing_value_rule="Zero for nodes that did not flood.",
)
_m(
    id="drainage.node_surcharge_duration",
    name="Node surcharge duration",
    unit="h",
    discipline="drainage",
    per_entity=True,
    definition="Time the water depth at a node exceeded the crown of the highest connected conduit.",
    calculation="Sum of report intervals with node depth > max(conduit offset + full height) over connected conduits.",
    source_outputs=_SW,
    extent="Per junction",
    time_interval="Simulation period",
    missing_value_rule="Zero for nodes that did not surcharge.",
)
_m(
    id="drainage.max_node_depth",
    name="Maximum node depth",
    unit="m",
    discipline="drainage",
    per_entity=True,
    definition="Maximum water depth above the node invert.",
    calculation="max of node INVERT_DEPTH series.",
    source_outputs=_SW,
    extent="Per node",
    time_interval="Simulation period",
    missing_value_rule="Never missing.",
)
_m(
    id="drainage.peak_outfall_flow",
    name="Peak outfall flow",
    unit="m3/s",
    discipline="drainage",
    per_entity=True,
    definition="Maximum total inflow to each outfall.",
    calculation="max of outfall TOTAL_INFLOW series.",
    source_outputs=_SW,
    extent="Per outfall",
    time_interval="Simulation period",
    missing_value_rule="Never missing.",
)
_m(
    id="drainage.conduit_max_fill",
    name="Conduit maximum fill",
    unit="1",
    discipline="drainage",
    per_entity=True,
    definition="Maximum ratio of flow depth to full depth in the conduit (conduit utilisation by depth).",
    calculation="max of link CAPACITY series (SWMM: depth / full depth).",
    source_outputs=_SW,
    extent="Per conduit",
    time_interval="Simulation period",
    missing_value_rule="Never missing.",
)
_m(
    id="drainage.conduit_max_flow_ratio",
    name="Conduit max/full flow",
    unit="1",
    discipline="drainage",
    per_entity=True,
    definition="Maximum flow divided by the conduit's full normal flow.",
    calculation="SWMM report Link Flow Summary 'Max/Full Flow'.",
    source_outputs=["model.rpt"],
    extent="Per conduit",
    time_interval="Simulation period",
    missing_value_rule="Unavailable for non-conduits.",
)
_m(
    id="drainage.max_storage_depth",
    name="Maximum storage depth",
    unit="m",
    discipline="drainage",
    per_entity=True,
    definition="Maximum water depth in each storage unit.",
    calculation="max of node INVERT_DEPTH for storage units.",
    source_outputs=_SW,
    extent="Per storage unit",
    time_interval="Simulation period",
    missing_value_rule="Not reported without storage units.",
)
_m(
    id="drainage.runoff_continuity_error",
    name="Runoff continuity error",
    unit="%",
    discipline="drainage",
    definition="SWMM runoff quantity continuity error.",
    calculation="Reported by SWMM.",
    source_outputs=["model.rpt"],
    extent="Model",
    time_interval="Simulation period",
    missing_value_rule="Unavailable without subcatchments.",
)
_m(
    id="drainage.routing_continuity_error",
    name="Flow routing continuity error",
    unit="%",
    discipline="drainage",
    definition="SWMM flow routing continuity error (solver stability indicator).",
    calculation="Reported by SWMM.",
    source_outputs=["model.rpt"],
    extent="Model",
    time_interval="Simulation period",
    missing_value_rule="Never missing.",
)

# ---------------------------------------------------------------- water supply (EPANET)
_EP = ["EPANET binary results via WNTR", "model.rpt"]
_m(
    id="water.min_pressure",
    name="Minimum pressure",
    unit="m",
    discipline="water_supply",
    per_entity=True,
    definition="Minimum pressure head at each junction over the simulation.",
    calculation="min of node pressure.",
    source_outputs=_EP,
    extent="Per junction",
    time_interval="Simulation period",
    missing_value_rule="Never missing.",
)
_m(
    id="water.network_min_pressure",
    name="Network minimum pressure",
    unit="m",
    discipline="water_supply",
    definition="Lowest junction pressure head anywhere in the network.",
    calculation="min of water.min_pressure.",
    source_outputs=_EP,
    extent="All junctions",
    time_interval="Simulation period",
    missing_value_rule="Never missing.",
)
_m(
    id="water.deficient_junctions",
    name="Junctions below service pressure",
    unit="count",
    discipline="water_supply",
    definition="Number of junctions whose minimum pressure falls below the scenario's service pressure threshold.",
    calculation="count(min pressure < threshold).",
    source_outputs=_EP,
    extent="All junctions",
    time_interval="Simulation period",
    missing_value_rule="Never missing.",
)
_m(
    id="water.max_velocity",
    name="Maximum pipe velocity",
    unit="m/s",
    discipline="water_supply",
    per_entity=True,
    definition="Maximum flow velocity in each pipe.",
    calculation="max |velocity| per pipe.",
    source_outputs=_EP,
    extent="Per pipe",
    time_interval="Simulation period",
    missing_value_rule="Never missing.",
)
_m(
    id="water.total_demand",
    name="Total demand delivered",
    unit="m3",
    discipline="water_supply",
    definition="Demand delivered to junctions over the simulation.",
    calculation="Sum over junctions of left-Riemann integral of demand (m3/s) over report steps.",
    source_outputs=_EP,
    extent="All junctions",
    time_interval="Simulation period",
    missing_value_rule="Never missing.",
)
_m(
    id="water.tank_level_range",
    name="Tank level range",
    unit="m",
    discipline="water_supply",
    per_entity=True,
    definition="Difference between maximum and minimum tank level.",
    calculation="max(level) - min(level).",
    source_outputs=_EP,
    extent="Per tank",
    time_interval="Simulation period",
    missing_value_rule="Not reported without tanks.",
)

# ---------------------------------------------------------------- operations (SimPy)
_OP = ["events.csv (SimPy event log)"]
_m(
    id="ops.completed",
    name="Completed entities",
    unit="count",
    discipline="operations",
    per_entity=True,
    definition="Entities that finished all process steps within the horizon (per flow), mean over replications.",
    calculation="count(finish event <= horizon) per replication; mean across replications.",
    source_outputs=_OP,
    extent="Per flow",
    time_interval="0 .. horizon",
    missing_value_rule="Never missing.",
)
_m(
    id="ops.unfinished",
    name="Unfinished entities",
    unit="count",
    discipline="operations",
    per_entity=True,
    definition="Entities that arrived within the horizon but did not finish, mean over replications.",
    calculation="arrivals - completed per replication; mean across replications.",
    source_outputs=_OP,
    extent="Per flow",
    time_interval="0 .. horizon",
    missing_value_rule="Never missing.",
)
_m(
    id="ops.mean_wait",
    name="Mean waiting time",
    unit="s",
    discipline="operations",
    per_entity=True,
    definition="Mean time between requesting and acquiring the resource, over granted requests.",
    calculation="mean(grant_time - request_time) per resource; mean across replications.",
    source_outputs=_OP,
    extent="Per resource",
    time_interval="0 .. horizon",
    missing_value_rule="Unavailable for resources with no granted requests.",
)
_m(
    id="ops.p95_wait",
    name="95th percentile waiting time",
    unit="s",
    discipline="operations",
    per_entity=True,
    definition="95th percentile of waiting time per resource (pooled across replications).",
    calculation="numpy percentile of pooled waits.",
    source_outputs=_OP,
    extent="Per resource",
    time_interval="0 .. horizon",
    missing_value_rule="Unavailable for resources with no granted requests.",
)
_m(
    id="ops.utilisation",
    name="Utilisation",
    unit="1",
    discipline="operations",
    per_entity=True,
    definition="Busy unit-time divided by available unit-time (capacity x horizon).",
    calculation="sum over holds of (min(release, horizon) - grant) / (capacity x horizon); mean across replications.",
    source_outputs=_OP,
    extent="Per resource",
    time_interval="0 .. horizon",
    missing_value_rule="Never missing.",
)
_m(
    id="ops.max_queue",
    name="Maximum queue length",
    unit="count",
    discipline="operations",
    per_entity=True,
    definition="Largest number of entities simultaneously waiting for the resource (max over replications).",
    calculation="Event-sweep of request/grant times.",
    source_outputs=_OP,
    extent="Per resource",
    time_interval="0 .. horizon",
    missing_value_rule="Zero when nobody waited.",
)
_m(
    id="ops.mean_queue",
    name="Time-average queue length",
    unit="count",
    discipline="operations",
    per_entity=True,
    definition="Time-weighted mean number waiting for the resource.",
    calculation="Integral of queue length over time / horizon; mean across replications.",
    source_outputs=_OP,
    extent="Per resource",
    time_interval="0 .. horizon",
    missing_value_rule="Zero when nobody waited.",
)
_m(
    id="ops.mean_cycle_time",
    name="Mean cycle time",
    unit="s",
    discipline="operations",
    per_entity=True,
    definition="Mean time from arrival to finishing all steps, over completed entities.",
    calculation="mean(finish - arrival) per flow; mean across replications.",
    source_outputs=_OP,
    extent="Per flow",
    time_interval="0 .. horizon",
    missing_value_rule="Unavailable when no entity completed.",
)

# ---------------------------------------------------------------- CFD (OpenFOAM)
_CF = ["OpenFOAM final time directory U field", "log.simpleFoam", "log.checkMesh"]
_m(
    id="cfd.max_speed",
    name="Maximum speed",
    unit="m/s",
    discipline="cfd",
    definition="Maximum cell velocity magnitude in the converged 2D field.",
    calculation="max |U| over cells.",
    source_outputs=_CF,
    extent="Fluid domain",
    time_interval="Final iteration (steady)",
    missing_value_rule="Unavailable if the solver did not write a field.",
)
_m(
    id="cfd.exceedance_area",
    name="Area above speed threshold",
    unit="m2",
    discipline="cfd",
    definition="Plan area of cells whose speed exceeds the scenario threshold (a defined geometric/kinematic "
    "measure, not a comfort or safety assessment).",
    calculation="sum of cell areas with |U| > threshold.",
    source_outputs=_CF,
    extent="Fluid domain",
    time_interval="Final iteration (steady)",
    missing_value_rule="Unavailable if no field was written.",
)
_m(
    id="cfd.final_residual_p",
    name="Final pressure residual",
    unit="1",
    discipline="cfd",
    definition="Initial residual of the pressure equation at the last iteration (convergence indicator).",
    calculation="Parsed from log.simpleFoam.",
    source_outputs=["log.simpleFoam"],
    extent="Solver",
    time_interval="Last iteration",
    missing_value_rule="Unavailable if the log has no residual lines.",
)
_m(
    id="cfd.mesh_cells",
    name="Mesh cells",
    unit="count",
    discipline="cfd",
    definition="Number of mesh cells.",
    calculation="Parsed from checkMesh.",
    source_outputs=["log.checkMesh"],
    extent="Mesh",
    time_interval="n/a",
    missing_value_rule="Never missing once meshed.",
)

# ---------------------------------------------------------------- thermal (EnergyPlus)
_EPL = ["eplusout.sql / eplustbl.csv"]
_m(
    id="thermal.peak_heating_load",
    name="Peak heating load",
    unit="W",
    discipline="thermal",
    definition="Maximum zone ideal-loads heating rate summed over zones.",
    calculation="EnergyPlus output.",
    source_outputs=_EPL,
    extent="Building",
    time_interval="Run period",
    missing_value_rule="Unavailable if not output.",
)
_m(
    id="thermal.peak_cooling_load",
    name="Peak cooling load",
    unit="W",
    discipline="thermal",
    definition="Maximum zone ideal-loads cooling rate summed over zones.",
    calculation="EnergyPlus output.",
    source_outputs=_EPL,
    extent="Building",
    time_interval="Run period",
    missing_value_rule="Unavailable if not output.",
)
_m(
    id="thermal.heating_energy",
    name="Heating energy",
    unit="kWh",
    discipline="thermal",
    definition="Ideal-loads heating energy over the run period.",
    calculation="EnergyPlus output.",
    source_outputs=_EPL,
    extent="Building",
    time_interval="Run period",
    missing_value_rule="Unavailable if not output.",
)
_m(
    id="thermal.cooling_energy",
    name="Cooling energy",
    unit="kWh",
    discipline="thermal",
    definition="Ideal-loads cooling energy over the run period.",
    calculation="EnergyPlus output.",
    source_outputs=_EPL,
    extent="Building",
    time_interval="Run period",
    missing_value_rule="Unavailable if not output.",
)

# ---------------------------------------------------------------- fire (FDS)
_m(
    id="fire.peak_hrr",
    name="Peak heat release rate",
    unit="kW",
    discipline="fire",
    definition="Maximum total heat release rate.",
    calculation="max of HRR column in CHID_hrr.csv.",
    source_outputs=["CHID_hrr.csv"],
    extent="Domain",
    time_interval="0 .. T_END",
    missing_value_rule="Unavailable if the HRR file is absent.",
)
_m(
    id="fire.device_max_temperature",
    name="Maximum device temperature",
    unit="degC",
    discipline="fire",
    per_entity=True,
    definition="Maximum temperature recorded by each temperature device.",
    calculation="max of device column in CHID_devc.csv.",
    source_outputs=["CHID_devc.csv"],
    extent="Per device",
    time_interval="0 .. T_END",
    missing_value_rule="Unavailable if the device file is absent.",
)
_m(
    id="fire.device_min_visibility",
    name="Minimum device visibility",
    unit="m",
    discipline="fire",
    per_entity=True,
    definition="Minimum visibility recorded by each visibility device.",
    calculation="min of device column in CHID_devc.csv.",
    source_outputs=["CHID_devc.csv"],
    extent="Per device",
    time_interval="0 .. T_END",
    missing_value_rule="Unavailable if the device file is absent.",
)

# ---------------------------------------------------------------- collision / CrashLens
_m(
    id="crash.rms_residual",
    name="RMS evidence residual",
    unit="m",
    discipline="collision",
    definition="Root-mean-square distance between modelled and measured evidence targets for a hypothesis's "
    "imported or computed trajectory (a consistency measure, not a probability).",
    calculation="sqrt(mean(residual_i^2)) over targets with a modelled position.",
    source_outputs=["trajectory (imported or engine)", "evidence observations"],
    extent="Hypothesis targets",
    time_interval="Trajectory end / closest approach",
    missing_value_rule="Unavailable without targets.",
)
_m(
    id="crash.targets_within_uncertainty",
    name="Targets within uncertainty",
    unit="count",
    discipline="collision",
    definition="Number of evidence targets whose residual is within the target's stated position uncertainty.",
    calculation="count(residual_i <= uncertainty_i).",
    source_outputs=["trajectory", "evidence observations"],
    extent="Hypothesis targets",
    time_interval="n/a",
    missing_value_rule="Unavailable without targets.",
)
_m(
    id="crash.skid_speed_low",
    name="Skid-to-stop speed (low friction bound)",
    unit="km/h",
    discipline="collision",
    definition="Speed at the start of a full-braking skid that ends at rest, using the low friction bound.",
    calculation="v = sqrt(2 x mu_low x g x d), g = 9.80665 m/s2, d = measured skid length. Assumes a full skid to "
    "rest on a level surface with constant deceleration; not valid if the vehicle struck something during the skid.",
    source_outputs=["skid mark observation", "friction range"],
    extent="One skid mark",
    time_interval="n/a",
    missing_value_rule="Unavailable without a measured skid length and friction range.",
)
_m(
    id="crash.skid_speed_high",
    name="Skid-to-stop speed (high friction bound)",
    unit="km/h",
    discipline="collision",
    definition="As crash.skid_speed_low with the high friction bound.",
    calculation="v = sqrt(2 x mu_high x g x d).",
    source_outputs=["skid mark observation", "friction range"],
    extent="One skid mark",
    time_interval="n/a",
    missing_value_rule="Unavailable without a measured skid length and friction range.",
)

# ---------------------------------------------------------------- other imported/exchange engines
_m(
    id="geomech.max_displacement",
    name="Maximum displacement",
    unit="m",
    discipline="geomechanics",
    definition="Maximum displacement magnitude at monitoring points, from the imported result table.",
    calculation="max over monitoring rows of displacement magnitude.",
    source_outputs=["imported result table"],
    extent="Monitoring points",
    time_interval="Final stage",
    missing_value_rule="Unavailable if not supplied.",
)
_m(
    id="vent.total_airflow",
    name="Total airflow",
    unit="m3/s",
    discipline="ventilation",
    definition="Sum of airflow through fan airways, from the imported Ventsim result table.",
    calculation="sum of |Q| over fan airways.",
    source_outputs=["imported airway table"],
    extent="Fans",
    time_interval="Steady state",
    missing_value_rule="Unavailable if not supplied.",
)
_m(
    id="hyd.max_depth",
    name="Maximum water depth",
    unit="m",
    discipline="surface_hydraulics",
    definition="Maximum depth over the imported HEC-RAS depth raster.",
    calculation="max over valid cells.",
    source_outputs=["imported max depth GeoTIFF"],
    extent="2D flow area",
    time_interval="Simulation period",
    missing_value_rule="Unavailable if not supplied.",
)
_m(
    id="hyd.inundated_area",
    name="Area deeper than 0.1 m",
    unit="m2",
    discipline="surface_hydraulics",
    definition="Area of cells with maximum depth above 0.1 m in the imported depth raster.",
    calculation="count(depth > 0.1) x cell area.",
    source_outputs=["imported max depth GeoTIFF"],
    extent="2D flow area",
    time_interval="Simulation period",
    missing_value_rule="Unavailable if not supplied.",
)
_m(
    id="rigid.freefall_time_error",
    name="Free-fall timing error",
    unit="s",
    discipline="rigid_body",
    definition="Difference between simulated first-contact time and the analytic free-fall time sqrt(2h/g) in the "
    "reference scenario.",
    calculation="t_sim - sqrt(2 h / g).",
    source_outputs=["states.csv"],
    extent="Reference body",
    time_interval="Until first contact",
    missing_value_rule="Unavailable if not run.",
)
_m(
    id="rigid.max_speed",
    name="Maximum body speed",
    unit="m/s",
    discipline="rigid_body",
    per_entity=True,
    definition="Maximum linear speed of each body.",
    calculation="max |v| from recorded states.",
    source_outputs=["states.csv"],
    extent="Per body",
    time_interval="0 .. duration",
    missing_value_rule="Unavailable if not run.",
)

METRICS: dict[str, MetricDefinition] = {m.id: m for m in _D}


def get(metric_id: str) -> MetricDefinition:
    return METRICS[metric_id]


def for_discipline(discipline: str) -> list[MetricDefinition]:
    return [m for m in _D if m.discipline == discipline]
