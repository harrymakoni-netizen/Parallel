# Data contracts

The contracts in `packages/contracts` are Pydantic v2 models shared by the API, the workers and the
engine adapters. They are the only shapes that cross component boundaries, and every revision stores
one of them as JSON with a SHA-256 of its content. Each top-level contract carries
`schema_version: "1.0"`. The machine-readable schemas are served by the API (`GET /api/v1/models/schemas`
and the [OpenAPI document](api/openapi.json)).

## Provenance

Every important value says where it came from (`provenance.py`).

| Kind | Meaning |
| --- | --- |
| `measured` | Measured, with a named instrument, survey or document as `source` |
| `imported` | Taken from a named external source or file |
| `assumption` | Entered as an assumption, with a note explaining it |
| `algorithm_estimate` | Estimated by a named algorithm (for example the reconstruction's ground plane) |
| `ai_suggested` | Suggested by the AI assistant; unusable until a person adopts it (`adopted_by`, `adopted_at`) |
| `derived` | Calculated from other recorded values, listed in `derived_from` |
| `synthetic` | Clearly identified synthetic demonstration material |

A `Provenance` also records `source_ref`, `recorded_by`, `recorded_at`, an optional `uncertainty`
(absolute, relative, range or standard deviation, with units) and a note. `SourcedQuantity` and `SourcedValue` attach a
provenance to a single number or value; `Assumption` is a named statement with rationale, the paths it
applies to and its own provenance. Adoption fields are valid only on AI-suggested values.

## Coordinates and geometry

- **Frames** (`coordinates.py`): `capture` (a reconstruction's own frame), `canonical` (the scene's
  working frame), `geographic` and `engine`. Every frame is right-handed, **Z up, in metres**.
- **Transforms** between frames are 4 x 4 matrices, row-major, applied to column vectors
  (`p_to = M @ [x, y, z, 1]`), with the method used and, where fitted, the RMS residual in metres.
- **Georeference**: a projected CRS for metric work (geographic CRSs are refused), the local origin in
  that CRS, a rotation, a vertical reference (or explicitly `unknown`) and its accuracy.
- **Geometry** (`geometry.py`): GeoJSON-like `Point`, `LineString`, `Polygon`, `MultiPolygon` and an
  oriented `Box`, in the canonical frame. Polygons are validated (closed rings, no self-intersection,
  holes inside shells, positive area) before any engine sees them, and walkable areas are checked for
disconnected components.

## Scenes

A scene revision (`SceneRevisionContent`, `scene.py`) holds:

| Field | Contents |
| --- | --- |
| `coordinates` | Frames, transforms and georeference |
| `visual_assets` | Point clouds, Gaussian splats, meshes and terrain, each in a named frame with quality notes; `generated` assets are allowed only in a `hypothetical` scene |
| `spatial_objects` | Prepared objects: kind (walkable area, obstacle, exit, refuge, start zone, route segment, road, junction, conduit and more), layer, geometry, properties (each a sourced value), provenance and `belongs_to`: `recorded_scene`, `prepared_model` or `hypothetical_scenario` |
| `measurements` | Point-to-point measurements with purpose, reference distance and the error against it |
| `annotations`, `viewpoints` | Notes and saved camera views |
| `assumptions`, `tolerance` | Scene-level assumptions and the measurement tolerance for its purpose |
| `quality` | Seven independently assessed quality states with evidence ([architecture](architecture.md#the-workflow-in-records)) |
| `provenance` | The scene's own provenance |

An object whose appearance is baked into a capture and which a scenario moves keeps both positions
(`embedded_in_capture`, `captured_geometry`), so the workspace can show where it still appears.

## Domain models

A `DomainModelEnvelope` (`domain/__init__.py`) is engine-neutral: discipline, name, the scene revision
it was prepared from, the coordinate frame, the model content, assumptions and provenance. The model
kind must belong to the discipline.

| Model kind | Discipline | Engines |
| --- | --- | --- |
| `pedestrian_geometry` | pedestrian | JuPedSim |
| `road_network` | traffic | SUMO |
| `drainage_network` | drainage | SWMM |
| `water_supply` | water supply | EPANET |
| `operations` | operations | SimPy |
| `fire` | fire | FDS |
| `cfd_wind2d` | cfd | OpenFOAM |
| `thermal` | thermal | EnergyPlus (with OpenStudio) |
| `geomech_continuum`, `geomech_discrete` | geomechanics | FLAC3D, 3DEC |
| `ventilation_network` | ventilation | Ventsim |
| `surface_hydraulics` | surface hydraulics | HEC-RAS |
| `rigid_body` | rigid body | Isaac Sim |
| `collision` | collision | Virtual CRASH, PC-Crash |

Physical parameters inside a model are `SourcedQuantity` values, so a report can count how many
inputs were measured, assumed, estimated or synthetic, and list any AI suggestion not yet adopted.

## Scenarios

`ScenarioContent` (`scenario.py`) names the discipline and engine, whether it is the baseline, the
discipline's **controls** (for example closed routes, population overrides, pre-movement distribution
and horizon for pedestrian runs; closed edges, lane and speed changes, junction control types, signal
programs, demand scale, horizon and warm-up for traffic; rainfall series and scale, conduit blockages
and resizes, storage conversions, low-impact development controls, pump and catchment changes for
drainage), run **settings** (seed, replications, timeout, solver options), assumptions,
and `model_change_notes`: the adapter's plain account of how each control changes the model. Controls
that are not implemented are refused rather than ignored (for example timed closures).

## Runs and results

A `SimulationRun` records the frozen scenario and model revisions, the scene revision, the engine,
execution mode, seed, settings, state history, attempts, worker, heartbeats, timings, the engine and
adapter versions, the environment and the hashes of every input. The result is a `ResultEnvelope`
(`runs.py`):

| Field | Contents |
| --- | --- |
| `revisions`, `engine`, `execution_mode`, `seed`, `solver_settings`, `environment` | What ran, and how |
| `input_hashes`, `output_hashes` | SHA-256 of every input file and output artifact |
| `metrics` | `MetricValue`s: metric id, value, unit, status (`available`, `unattained`, `unavailable`, `not_applicable`), entity for per-entity metrics, replicate and replicate statistics, note |
| `trajectories`, `timeseries`, `fields` | References to stored artifacts with their kind, units and hashes |
| `validation`, `warnings` | The pre-run validation report and anything the engine or adapter reported |
| `native_crosschecks` | Comparisons between normalised values and the engine's own outputs, with tolerances and pass or fail |
| `processing_history` | Each processing step with its tool and version |
| `imported` | For file-exchange results: the external installation, who supplied the package, and when |

### Normalisation

Each adapter turns its engine's native outputs into the envelope:

1. **Collect** the native outputs (for example JuPedSim exit records and trajectories, SUMO trip info
   and floating-car data, SWMM's report and binary output, SimPy's event log) and store them unchanged
   as `raw output` artifacts with their hashes.
2. **Normalise** them into metrics from the [metric catalogue](metrics.md) (an unknown metric id fails
   the run), time series and trajectories, in SI units in the canonical frame.
3. **Cross-check**: recompute key values from the native outputs independently (for example the evacuated
   count from the runner's exit records against the normalised total, or SWMM's flooding and runoff
   volumes against its own report summaries) and
   record each comparison with its tolerance. A failed cross-check is shown with the result.
4. **Derive** browser-friendly artifacts where needed: `playback.json` samples trajectories at
   about 0.5 s with centimetre precision for display only; metrics always use the original outputs.

Values that an engine did not reach within the horizon are `unattained` (for example T100 when not
everyone evacuated), and values it could not provide are `unavailable`; neither is ever estimated.
Replicated stochastic runs report each replicate and their distribution.

### Artifacts

Every artifact has a name, kind (`input`, `raw output`, `log`, `normalised`, `timeseries`,
`trajectory`), media type, size, SHA-256 and description, and is downloadable through a signed URL.

## Comparisons

A comparison holds the baseline and candidate runs, the result of every compatibility check (engine
and adapter versions, horizon, initial conditions, population or demand, seeds, boundary conditions,
model revisions) and a table of differences per metric and entity: absolute difference and, where
meaningful, percentage difference, with a note when either is withheld (unavailable values, zero
baseline, incompatible units, interval-scale units). It is exportable as CSV.

## Reports

A report is built from one **manifest** (`workers/results/.../manifest.py`), and the PDF, CSV and JSON
are rendered from that same manifest so they cannot disagree:

- the project, author, generation time and review status;
- each run: scenario and model revisions with numbers and hashes, engine and adapter versions, seed,
  execution mode, metrics with names and statuses, cross-checks, warnings, model change notes;
- the comparison and its compatibility checks, where there is one;
- a provenance summary: counts by kind, notable inputs with uncertainty, scenario assumptions, whether
  synthetic material is present, and any unadopted AI suggestion;
- reviews of the objects in the report;
- for CrashLens case reports: evidence with hashes and handling, observations, interpretations with the
  observations they rely on, hypotheses with evidence links and analyses, and the statement that the
  report does not determine fault.

The PDF template (`workers/results/.../pdf.py`) uses the supplied logo unaltered, charts with a table
twin for every chart, and short references (E1, O1, I1, H1) with full identifiers listed. Narrative text
is either written by a person or an adopted AI draft, and the report records which.

## CrashLens records

`crashlens.py` and the case models hold cases, assignments, evidence items (original asset, SHA-256 at
receipt, capture claims, chain of custody), derivatives (redaction, enhancement, annotation; each with
its own hash), observations (statement type `observation`), interpretations (statement type
`interpretation`, with `based_on` listing the observations used), vehicles, witness statement
references, a timeline, hypotheses with evidence links (`supports`, `contradicts`, `context`) and
analyses that tie a run to a hypothesis.
