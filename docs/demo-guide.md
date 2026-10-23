# Demonstration guide

Parallel ships five prepared demonstration projects, one per module. They are built from **clearly
identified synthetic material**: synthetic plans, a synthetic road scene rendered for reconstruction,
synthetic evidence photographs and synthetic demand, storms and populations. No real site, incident or
person is represented. Their results come from **real recorded runs** of the engines named, labelled
in the application as *Saved result (recorded <date>)*. Nothing is pre-drawn and no timer imitates
computation.

## Setting up

```bash
PARALLEL_DEMO_PASSWORD=choose-a-password uv run python -m parallel_api.demo seed
uv run python -m parallel_api.demo status     # what exists, with run and report counts
uv run python -m parallel_api.demo reset      # delete and rebuild the five demonstration projects
```

In the container deployment: `docker compose run --rm sim python -m parallel_api.demo seed` (the worker
image has the engines). Seeding creates the organisation *Parallel Demonstrations* with two accounts:

| Account | Role |
| --- | --- |
| `presenter@parallel-demo.example` | Organisation administrator, investigator on the CrashLens case |
| `reviewer@parallel-demo.example` | Reviewer on every demonstration project and the case |

The password is `PARALLEL_DEMO_PASSWORD` when set; otherwise a random one is generated and printed once.
`reset` deletes only projects marked as demonstrations (derived evidence before originals, so evidence
integrity rules hold) and never touches other projects or accounts. On a 4-CPU host seeding takes
about a minute, most of it the CrashLens reconstruction.

Each project contains its source information and presenter notes as documents (Captures page), at
least one saved scene revision, prepared models with provenance, scenario descriptions, saved results,
a comparison and a generated report (PDF, CSV and JSON).

## The five demonstrations

### Mine Evacuation: *Level 3 mine section: evacuation*

Comparison: baseline, blocked route and proposed alternative (JuPedSim 1.4.2, 60 people, seed 42).

1. **Scene.** Open *Level 3 section plan*: walkable drift, portals, refuge and three crew start zones.
   These are prepared objects on a synthetic plan, not a capture. Revision 2 adds the proposed
   escapeway as a hypothetical object (violet, dashed).
2. **Prepare.** The movement model was derived from the scene revision; its quality panel shows the
   model, and the runs that verified the integration.
3. **Scenarios.** *Baseline*; *West drift blocked*, unavailable from the start; *Proposed: new
   escapeway*, a layout alternative on its own model revision. Same seed and population throughout.
4. **Results.** Play the evacuation back in the workspace (side by side with another run). Show the
   evacuated-over-time curves and the comparison: T100 50.22 s baseline, 66.49 s with the west drift
   blocked, 50.84 s with the escapeway; T95 rises by 35 per cent when blocked and falls below baseline
   with the escapeway. Point out the accounting identity: initial population = evacuated + unreachable +
   remaining.
5. **Honesty.** If anyone remains at the horizon, T100 is shown as unattained and never replaced by the
   last exit time. The proposal is shown as computed; it is not required to win.
6. **Report.** Revisions, engine versions, seeds and input hashes for every run.

### CrashLens: *Training junction collision*

Comparison: captured scene, evidence annotations and alternative hypotheses.

1. **Reconstruction.** Open the reconstruction job: four deliberately blurred frames rejected, COLMAP
   registering the remaining 32, scale fitted to surveyed positions with ten held out as checks. On a
   host without a GPU the Gaussian training stage is skipped and the untrained initialisation is
   labelled as such. The workspace shows the point cloud, the quality states with their evidence and
   the measurement checks.
2. **Evidence.** Three photographs stored as originals with SHA-256 hashes; verify one live ("Unchanged
   since receipt"). A hash shows the file is unchanged since receipt, not that its content or timestamp
   is authentic; capture times are shown as stated, unverified.
3. **Observations and interpretations.** The skid mark, rest positions, debris and road condition are
   observations. The skid-to-stop speed range is an interpretation that cites the skid observation and
   states its limits.
4. **Hypotheses.** H1 and H2 side by side with supporting, contradicting and context links. There is no
   probability of truth, and evidence fit is "not evaluated" until a collision-engine result (Virtual
   CRASH or PC-Crash, by file exchange) is imported.
5. **Access.** Only assigned investigators and reviewers see the case and anything derived from it;
   other project members do not see it at all, not even in counts or activity.
6. **Report.** The case report carries evidence identifiers, hashes, observation and hypothesis
   references, and states that it does not determine fault.

### Construction Sites: *Riverside block: site logistics*

Comparison: existing layout, operational alternative and evacuation scenario.

1. **Scene.** The plan shows the yard, building footprint, gates, crew zones and logistics points.
2. **Operational alternative.** *Existing layout deliveries* against *Second loading bay for concrete
   pours* (SimPy 4.1.2, ten replications with common random numbers); show the replicate statistics.
3. **Conservation.** Every request is granted, waiting or unfinished, cross-checked against the event log.
4. **Evacuation.** The drill scenario uses the movement model derived from the same plan scene.
5. **Reports.** One for the operational comparison and one for the drill.

### Urban Planning: *Crossroads corridor: junction control*

Comparison: baseline transport arrangement and proposed change (SUMO 1.28.0).

1. **Scene.** Corridor plan with road centrelines and the central junction.
2. **Scenarios.** Priority junction (baseline) and signalised junction (proposed), same demand and seed.
3. **Results.** Mean travel time and delay are read alongside the completion ratio: a lower mean travel
   time that coincides with fewer completed trips is flagged as not an improvement. SUMO's own trip
   statistics are cross-checked against the normalised values.
4. **Playback.** Vehicle positions over an hour come from SUMO's floating-car output.

### Urban Drainage: *Mill Lane catchment: flooding*

Comparison: existing network, constrained condition and proposed intervention (SWMM 5.2.4).

1. **Scene.** The network plan shows junctions, conduits and the outfall.
2. **Scenarios.** Existing network, a silted conduit (constrained) and storage at J2 (intervention), all
   under the same 60-minute design storm; every run records the rainfall hash, identical across the
   three runs.
3. **Results.** Flooding volume and duration per node, conduit surcharge, system flooding and rainfall
   charts; volumes are cross-checked against SWMM's own report tables.
4. **Limits.** Node flooding is not a surface flood map; a two-dimensional model (HEC-RAS by file
   exchange) and terrain are needed for that, and the application does not animate one from SWMM.

## Suggested running order

About fifteen minutes: Demonstrations page (synthetic labels) → Mine Evacuation scene and playback →
comparison and report → CrashLens evidence, interpretation and hypotheses → Urban Planning playback and
the completion-ratio guard → Engine Library (honest availability of licensed engines) → the assistant
drafting a scenario that only takes effect once adopted (requires an API key).

## Things not to claim

- That a reconstruction validates geometry or physics, or that a proposal "wins" before its run says so.
- That a hash proves authenticity, or that a hypothesis is more likely: Parallel computes neither.
- That a licensed engine ran when it did not: the Engine Library shows each installation's real state.
- That the demonstrations represent a real site or incident.
