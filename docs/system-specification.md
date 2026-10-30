# Parallel: system specification (as built)

| | |
| --- | --- |
| **Document** | System specification for Parallel, version 1.0 |
| **Date** | 28 October 2026 |
| **Code** | `github.com/harrymakoni-netizen/Parallel`, branch `claude/gracious-allen-6xoln9` |
| **Audience** | Engineers taking over or extending the system, reviewers, and anyone judging what Parallel does and does not do |
| **Relation to other documents** | The [development specification](PARALLEL_DEVELOPMENT_SPEC.md) is the requirement set the project was built to. **This document describes the system as implemented**, and states for every area whether the requirement is met. It does not replace the generated references ([API](api.md), [metric catalogue](metrics.md), [engine matrix](engine-matrix.md)), which are exact and are the authority on their subjects |
| **Order of authority** | Where documents disagree: the code and its tests, then the generated references, then [implementation status](implementation-status.md), then this document |

**Status words used throughout** (the same categories as the implementation status):
**Verified** (built and exercised by automated tests or a recorded end-to-end check), **Awaiting environment**
(built and tested as far as possible, but needs an engine installation, licence or GPU that was not available),
**In progress** (partly built; the gap is stated), **Blocked** (waiting on a named external item), **Not implemented**.

## Contents

1. Purpose, scope and principles
2. Users, roles and access
3. Product structure
4. Capture and reconstruction
5. Scenes, models and scenarios
6. Engine framework and catalogue
7. Runs, results and metrics
8. Comparison, optimisation and reports
9. The five modules
10. The AI assistant
11. Data specification
12. Architecture and technology
13. API specification
14. Non-functional requirements
15. Deployment and operations
16. Verification
17. Status against the development specification, limitations and roadmap
18. Appendices

---

## 1. Purpose, scope and principles

### 1.1 Purpose

Parallel takes a recorded environment to a documented decision:
**Capture, Reconstruct, Prepare, Simulate, Compare, Improve.**

Photographs, video or drone footage of a real place become a reconstructed scene that can be inspected and measured.
Simplified, editable models are prepared from it with the provenance of every input recorded. Established engineering
engines run scenarios on those models; the results are normalised, cross-checked against each engine's own output, and
compared under controlled conditions; reports state what was tested and what the results support.

### 1.2 The governing rule

**An impressive picture is never presented as a calculation.** A visual reconstruction is never treated as physical
validation, an unavailable engine is reported as unavailable, metrics come only from real engine runs, and AI suggestions are
used only after a person adopts them.

### 1.3 Modules and scope

| Module | Question it answers |
| --- | --- |
| Mine Evacuation | How long does a section take to clear, and what changes it: blocked routes, refuges, escapeways? |
| CrashLens | Which hypotheses about a collision does the evidence support, and how closely does each fit the measurements? |
| Construction Sites | How do site logistics and evacuation perform under alternative layouts? |
| Urban Planning | How does a transport, public-space or building change perform, without mistaking unfinished trips for gains? |
| Urban Drainage | Where does a network flood under a design storm, and does an intervention help? |

All fifteen engines of the development specification have an adapter. **Six run for real in the supplied worker image**; the others
run through file exchange, need a licence or a GPU, or are incomplete (section 6.4).

### 1.4 Out of scope (not built, or deliberately not done)

- Coupled multi-engine simulation (one-way exchange, iterative coupling, co-simulation).
- Calibration of a model against observations (the quality state is never set automatically).
- Inferring hidden infrastructure, rock strength, vehicle speed, traffic demand or fire properties from imagery.
- Determining fault, or giving a probability that a collision hypothesis is true.
- Running uploaded code, scripts or solver plugins.
- Generating scene content with AI (generated content is permitted only in hypothetical scenarios, and Parallel does not itself generate any).

### 1.5 Non-negotiable engineering rules

These come from the development specification, are listed in `CLAUDE.md`, and are enforced by tests. They are requirements, not guidance.

| ID | Rule | How it is enforced |
| --- | --- | --- |
| P-1 | **No invented results.** Metrics come only from engine outputs through an adapter's normaliser. Unattained or unavailable values are reported as such, never estimated. No timers or animations stand in for work | A metric registry (72 ids); an unknown id fails the run; native cross-checks on every normalised run; `unattained` and `unavailable` are distinct statuses |
| P-2 | **Honest availability.** An engine that is not installed, licensed or connected shows its real state and a next step. Never fall back to another engine or a stub silently | Availability is computed from real checks (worker-reported for managed engines); a run on an unavailable engine is refused with the engine's state |
| P-3 | **Provenance on every input.** Values in models and scenes carry a provenance. AI-suggested values stay `ai_suggested` until a person adopts them; runs refuse unadopted AI content | Seven provenance kinds in the contracts; the adoption gate in run submission; reports count inputs by kind |
| P-4 | **Reproducibility.** Published revisions are immutable; edits create new revisions. Runs record revisions, hashes of every input and output, seeds, engine and adapter versions | SHA-256 over revision content; frozen scenario and model revisions per run; the result envelope |
| P-5 | **Visual is not physical.** Captured appearance is never treated as walkable or solid geometry; prepared objects are separate, simplified and editable. A reconstruction never implies scale, measurement or calibration quality | Captured, prepared and hypothetical content are distinct records; seven independently assessed quality states; derived models use only prepared objects |
| P-6 | **CrashLens neutrality.** Observations and interpretations are separate and interpretations cite the observations they use. Parallel never computes a probability that a hypothesis is true and never determines fault. Evidence originals are immutable and hashed at receipt | Distinct statement types with `based_on` links; database triggers refuse changes to originals; reports carry the no-fault statement |
| P-7 | **Access is decided on the server** for every request: organisation and project roles, and case assignment for anything case-scoped. Lists and counts must not leak case records | `require_project`, `require_case`, `require_scoped`; inaccessible records answer 404 |
| P-8 | Do not copy code from the reference Gaussian Splatting repository (it has no licence). The reconstruction pipeline is an independent implementation and credits it | Code review; credit in the README and documentation |
| P-9 | The logo (`brand/parallel-logo.webp` and its copies) is used unaltered and never redrawn | A test checks the packaged copy is byte-identical to the supplied file |

---

## 2. Users, roles and access

### 2.1 Roles (as implemented)

| Level | Role | Permitted actions |
| --- | --- | --- |
| Organisation | `admin` | Manage membership, integrations and organisation settings; acts as `org_admin` on every project (view, edit, review, manage) |
| Organisation | `member` | Sees only projects they are a member of |
| Project | `editor` | View and edit: upload captures, edit models, create scenarios, run simulations |
| Project | `reviewer` | View and review models, assumptions and reports |
| Project | `viewer` | View permitted scenes and results |
| Case | `investigator` | View and edit an assigned CrashLens case |
| Case | `reviewer` | View and review an assigned case's evidence annotations, hypotheses and report |

### 2.2 Access rules

| ID | Requirement | Status |
| --- | --- | --- |
| A-1 | Every request is authorised on the server. A user in another organisation, or without membership, receives 404 so the existence of a record is not revealed; a user whose role is insufficient receives 403 with the reason | Verified |
| A-2 | **Case access requires explicit assignment**, including for organisation administrators. Ordinary access to an organisation does not expose investigations | Verified |
| A-3 | Case-derived models, scenarios, runs, counts, activity feeds and reports are hidden from people outside the case | Verified |
| A-4 | Evidence media is not reachable through ordinary asset routes, only through its case | Verified |
| A-5 | Review records state what was reviewed (geometry, evidence annotations, model assumptions, interpretation); a review never implies broader certification | Verified |
| A-6 | Removing a member removes access | Verified |
| A-7 | Downloads use short-lived signed URLs (HMAC for local storage, presigned for S3); a tampered signature is refused | Verified |
| A-8 | Archived projects are inaccessible to ordinary use | Verified |
| A-9 | In open access mode the guest is authorised like any user: member of a sandbox organisation only, no other organisation, no administration, no case access, no external AI, bounded uploads, storage and reconstructions | Verified |

### 2.3 Identity

- **Local accounts** (`PARALLEL_AUTH_MODE=local`): email and password (scrypt hashes, 12-character minimum when created by the admin command), signed session tokens valid 12 hours by default. Verified.
- **OIDC**: the API verifies tokens from an identity provider's published keys (`oidc`, issuer, audience, JWKS URL). **The browser sign-in flow, account linking and a test are not implemented.** In progress.
- **Open access** (`PARALLEL_OPEN_ACCESS`, off by default): a request with no token is served as one shared guest user, a plain member of a sandbox organisation, so the site works with no sign-in. A token still means that token's user, and an invalid token is refused. See [deployment.md](deployment.md#open-access-no-sign-in). Verified.
- Operator commands (`python -m parallel_api.admin`): `create-admin` (creates the user if absent and makes them administrator of a named organisation, creating it if absent) and `set-password`.

---

## 3. Product structure

### 3.1 Navigation (as built)

| Scope | Sections |
| --- | --- |
| Global | Projects, Demonstrations, Engine Library, Reports, Organisation Settings |
| Inside a project | Overview, Captures, Scene Workspace, Models, Scenarios, Runs, Compare, Reports, Activity |
| CrashLens projects, additionally | Evidence, Timeline, Hypotheses, Case Review |

The assistant is available beside the workspace. Status: Verified.

### 3.2 Project creation

A project records: name, project type (`mine_evacuation`, `crashlens`, `construction`, `urban_planning`, `urban_drainage`, `general`),
description, optional location (label and longitude and latitude), optional coordinate information (a **projected** CRS and local origin; geographic CRSs
are refused for metric work), membership, and an optional **starter template** (one per module) that adds clearly labelled synthetic starter models and
scenarios and never adds results. Users can start from captures, existing model files or a template. Status: Verified.

### 3.3 Interface requirements

| ID | Requirement | Status |
| --- | --- | --- |
| U-1 | Supplied logo used unaltered; dark theme by default, light by preference or toggle; reviewed in both | Verified |
| U-2 | Scene workspace: viewport, layers and objects panel, properties panel with provenance, playback timeline with side-by-side comparison of two runs, orbit, pan, zoom, top-down, fit, named viewpoints, layer visibility, selection, measurement with reference checks, CrashLens evidence markers, saved images, quality and measurement panels | In progress: placing new annotation markers from the toolbar, and opening the assistant in the context of a selected object, are missing |
| U-3 | Captured, model and combined views; a moved object whose appearance is baked into a capture shows its captured position in red with a warning | Verified |
| U-4 | Scene frame: right-handed, Z up, metres | Verified |
| U-5 | Accessibility: keyboard navigation, focus indicators, skip link, one main landmark and one level-one heading per page, status always as icon plus text, a table view for every chart, reduced motion respected, non-3D access to all key information | Verified on desktop in both colour schemes; **tablet and phone layouts exist but are untested, and no formal WCAG 2.2 AA audit has been done** |
| U-6 | British English in user-facing text | Verified |

---

## 4. Capture and reconstruction

### 4.1 Supported inputs

| Input | Formats | Status |
| --- | --- | --- |
| Photographs, drone stills | JPEG, PNG, TIFF | Verified (reconstruction tests) |
| Video, drone video | MP4, MOV, M4V, MKV, AVI (FFmpeg and ffprobe) | Implemented; verified by hand on the deployed server with a phone video; **no automated test uses video input** |
| Gaussian splats | PLY (Inria layout) verified; SPLAT, SPZ, KSPLAT accepted and passed to the viewer by name | PLY verified; the others untested |
| Point clouds | PLY (drawn); LAS and LAZ (accepted and stored, not drawn) | PLY verified |
| Meshes | glTF, GLB, OBJ, STL (stored, not drawn) | Display not implemented |
| Terrain | GeoTIFF with a CRS (inspected with rasterio) | Stored and inspected; display and terrain-to-model alignment not implemented |
| Domain model files | Parallel model envelope (JSON); SWMM `.inp`, SUMO networks and routes, EPW weather accepted and stored | Envelope import verified; native files are stored, not converted |
| Result packages | ZIP per engine exchange format | Verified per engine |

Uploads are streamed with a size limit (default 4 GiB), hashed (SHA-256) at receipt, checked against content signatures,
inspected, and stored once as originals that are never overwritten. Guided capture instructions are served by the API.

### 4.2 Pipeline

A reconstruction job runs eleven **checkpointed** stages; each stores its outputs to object storage, so a job resumes from the last valid stage after a
failure or cancellation. Progress is available as server-sent events.

| # | Stage | What it does |
| --- | --- | --- |
| 1 | Upload verification | Re-hashes stored inputs against the receipt hash |
| 2 | Media inspection | Image and video properties |
| 3 | Frame extraction | FFmpeg samples video at the configured rate; images are normalised |
| 4 | Frame filtering | Rejects blur (Laplacian variance, an absolute floor and a floor relative to the median) and near-duplicates (perceptual hash); thins to the frame limit |
| 5 | Pose estimation | COLMAP feature extraction, matching and incremental mapping; a minimum registered fraction is required |
| 6 | Training | Splatfacto (nerfstudio) on a GPU worker. **Without a GPU the stage records why and is skipped**; the next stages publish an *untrained initialisation*, labelled as such |
| 7 | Visual export | Sparse point cloud PLY and Gaussian PLY |
| 8 | Geometry generation | Dominant ground plane by RANSAC (an algorithm estimate); a collision mesh only where Open3D is installed |
| 9 | Scale and orientation | With surveyed reference positions: a similarity transform (Umeyama) with residuals reported and every third position held out as an independent check. Without them: orientation from the ground plane only, **scale recorded as unknown** |
| 10 | Quality assessment | Registered fraction, reprojection error, alignment residuals, and independent distance checks against the scene's stated tolerance |
| 11 | Scene publication | A scene revision with visual assets, transforms, measurements, quality states and provenance |

**Defaults** (`pipeline.py`): 2 frames per second; blur floor 15 and 35% of the median sharpness; duplicate hash distance 3; at most **240** frames (more are
thinned evenly); at least 60% of frames registered; tolerance 0.05 m with its stated purpose; Splatfacto preset `refined`.
Synthetic demonstration captures are labelled `synthetic` in their provenance.

### 4.3 Outputs and quality

Outputs are a sparse point cloud, camera poses, an untrained or trained Gaussian layer, a ground plane, transforms between frames (capture, canonical),
measurement checks, and a quality assessment. A reconstruction never receives every quality state automatically (section 5.2).

### 4.4 Training a splat outside Parallel (external training)

Added for GPUs that are not connected to a deployment (free notebook GPUs, rented cards). Full guide: [external-training.md](external-training.md).

| ID | Requirement | Status |
| --- | --- | --- |
| E-1 | A completed reconstruction can export a **training package** (accepted frames, COLMAP model in text form, `manifest.json` with SHA-256 of every file, README), stored as a hashed asset; building it again returns the same asset | Verified |
| E-2 | A Colab and Kaggle notebook (generated from `scripts/training_notebook/`) verifies the package, trains Splatfacto with orientation, centring and scaling off so the splat stays in the reconstruction's frame, converts back using `dataparser_transforms.json` if needed, checks alignment, and produces `splat.ply` and `training_info.json` | Helper code verified; **the training commands have never run on a GPU** |
| E-3 | **Import** validates the PLY (binary little-endian, Gaussian properties, finite values, size matching the header, at most 15 million Gaussians), and **refuses a splat not in the reconstruction's frame**: the median distance from sparse points to the nearest Gaussian must be at most 5% of the scene spread and the spread ratio within 0.5 to 2, with the measurements returned | Verified |
| E-4 | `training_info.json` is checked: its job must match, and its package hash must match the one Parallel issued; unknown fields are ignored | Verified |
| E-5 | An accepted splat becomes a **new scene revision**; earlier revisions and the untrained layer are unchanged. It is recorded with provenance `imported`, labelled *trained outside Parallel*, with the trainer, GPU, settings, importer, alignment result, and a note that it is appearance only. No quality state is granted by importing | Verified |
| E-6 | The exact same file cannot be imported twice into one scene; an audit event records each import | Verified |

---

## 5. Scenes, models and scenarios

### 5.1 Coordinates and units

- Frames: `capture` (a reconstruction's own), `canonical` (the working frame), `geographic`, `engine`. All right-handed, **Z up, metres**.
- Transforms: 4 x 4 row-major matrices applied to column vectors, with the method and RMS residual where fitted; shear is refused.
- Georeference: a projected CRS, local origin, rotation, vertical reference (or explicitly `unknown`) and accuracy.
- Units are SI at the contract boundary; the unit registry checks dimensions.
- Geometry: Point, LineString, Polygon, MultiPolygon and oriented Box; polygons are validated (closed rings, no self-intersection, holes inside shells, positive area) and walkable areas are checked for disconnected components before any engine sees them.

### 5.2 Scene revisions and quality states

A scene revision holds coordinates, visual assets (each in a named frame), prepared spatial objects, measurements, annotations, viewpoints, assumptions, tolerance, quality, and provenance.
Content is stored as JSON with a SHA-256; editing a published revision creates a new one.

| Object belongs to | Meaning |
| --- | --- |
| `recorded_scene` | Captured or surveyed content |
| `prepared_model` | Simplified, editable analytical geometry used by engines |
| `hypothetical_scenario` | Proposals; drawn differently (violet, dashed) and excluded from derived models unless requested |

**Seven quality states**, each assessed on its own and never granted wholesale: visual reconstruction available; scale established; measurements checked; domain model prepared; model reviewed; simulation integration verified; model calibrated against observations. The first three are recorded by the pipeline or alignment; the next three are read from the models, reviews and runs that cite the revision; **calibration is never inferred**.

### 5.3 Scene preparation and editing

| Capability | Status |
| --- | --- |
| Object kinds: walkable areas, obstacles, exits, refuges, zones, routes, roads, junctions, conduits, vehicles; properties with provenance; `belongs_to` | Verified |
| Revision creation; alignment (control points or a known distance); geometry validation before simulation (self-intersection, drainage, water and road network connectivity, required physical properties, flags on non-specialist geomechanics properties) | Verified |
| Model import from files and editing as JSON; deriving a movement model from prepared objects | Verified |
| Interactive drawing and editing of geometry in the 3D view; undo and redo; network editing tools; export of scenes to other formats | **Not implemented** |

### 5.4 Domain models

An engine-neutral `DomainModelEnvelope` names the discipline, the scene revision it came from, the frame, the content, assumptions and provenance. Physical parameters inside a model are sourced quantities, so a report can count how many inputs were measured, assumed, estimated or synthetic.

| Model kind | Discipline | Engines |
| --- | --- | --- |
| `pedestrian_geometry` | pedestrian | JuPedSim |
| `road_network` | traffic | SUMO |
| `drainage_network` | drainage | SWMM |
| `water_supply` | water supply | EPANET |
| `operations` | operations | SimPy |
| `fire` | fire | FDS |
| `cfd_wind2d` | cfd | OpenFOAM |
| `thermal` | thermal | EnergyPlus with OpenStudio |
| `geomech_continuum`, `geomech_discrete` | geomechanics | FLAC3D, 3DEC |
| `ventilation_network` | ventilation | Ventsim |
| `surface_hydraulics` | surface hydraulics | HEC-RAS |
| `rigid_body` | rigid body | Isaac Sim |
| `collision` | collision | Virtual CRASH, PC-Crash |

### 5.5 Scenarios

A scenario revision names the discipline and engine, whether it is the baseline, the discipline's **controls**, run **settings** (seed, replications, timeout, solver options), assumptions,
and `model_change_notes` (the adapter's plain account of how each control changes the model). **Controls that are not implemented are refused, not ignored** (for example timed closures).

| Discipline | Controls implemented |
| --- | --- |
| Pedestrian | Closed route segments, population overrides, pre-movement distribution, horizon |
| Traffic | Closed edges, lane and speed changes, junction control types, signal programs, demand scale, horizon, warm-up |
| Drainage | Rainfall series and scale, conduit blockages and resizes, storage conversions, low-impact development controls, pump and catchment changes |

---

## 6. Engine framework and catalogue

### 6.1 Adapter interface

Every engine has an adapter in `packages/engines` that declares: a **capability record**, an **installation check**, **input materialisation** from the model and scenario using controlled templates (hashed), **execution** through a runner, **normalisation** into registered metrics, and **native cross-checks**. Engines run in isolated runtimes (`scripts/setup_runtimes.sh` creates them under `.runtimes/`) as separate process groups with CPU, memory, output-size and wall-clock limits, as an unprivileged user in the container images. Uploaded code is never executed.

### 6.2 Execution modes and availability

| Mode | Where the engine runs | Availability comes from |
| --- | --- | --- |
| Managed | A Parallel worker, from a pinned runtime | The workers check their managed engines at start-up and every five minutes and record the result; the API uses a report no older than 15 minutes when its own host cannot run the engine |
| Connected | An organisation's own installation reached by a registered connector | The registered installation and its last check |
| File exchange | A qualified person outside Parallel | The registered installation and licence confirmation. Parallel prepares an input package; the run waits in `awaiting_external_result`; the operator uploads a result package, which is validated (traversal, absolute paths, symlinks, member size, total size, compression ratio), normalised and labelled as imported |

### 6.3 Capability records

Each record states: role, vendor, licence, adapter and pinned versions, disciplines, dimensions, model input schema, outputs, units, modes, limitations, and platform support.
The full generated table is the [engine matrix](engine-matrix.md).

### 6.4 The fifteen engines

| Engine | Role | Modes | Tested version | Status on the reference server |
| --- | --- | --- | --- | --- |
| JuPedSim | Pedestrian movement and evacuation (one approximately level section, CollisionFreeSpeedModel) | Managed | 1.4.2 | **Verified, runs** |
| Eclipse SUMO | Road traffic | Managed | 1.28.0 | **Verified, runs** |
| EPA SWMM | Urban runoff and drainage networks | Managed | 5.2.4 (swmm-toolkit 0.17.0) | **Verified, runs** |
| EPA EPANET | Pressurised water networks | Managed | 2.2 via WNTR 1.5.0 | **Verified, runs** |
| SimPy | Discrete-event operations | Managed | 4.1.2 | **Verified, runs** |
| OpenFOAM | Two-dimensional site wind from approved case templates only | Managed | v1912 | **Verified, runs** |
| EnergyPlus with OpenStudio | Building energy and thermal analysis | Managed, file exchange | OpenStudio 3.11.0; EnergyPlus 25.2.0 | OpenStudio model preparation verified; **EnergyPlus execution awaiting environment** |
| NIST FDS | Fire-driven smoke and heat | Managed, file exchange | 6.10.1 (format) | Input generation and result import ready; **awaiting environment** |
| HEC-RAS | River and surface-flow hydraulics | File exchange | 6.6 (format) | Exchange and raster import ready; **awaiting environment** |
| NVIDIA Isaac Sim | Robots, equipment, rigid-body interaction | Managed on a GPU worker; connected | 5.x | **Awaiting environment (RTX GPU)**; connection protocol for remote hosts not implemented |
| Virtual CRASH | Specialist collision analysis | File exchange | unspecified | Import implemented against its exchange format; **awaiting a licence** |
| Ventsim | Mine ventilation | File exchange | unspecified | **Awaiting a licence**; not verified |
| FLAC3D | Continuum geomechanics | File exchange | unspecified | **Awaiting a licence**; not verified |
| 3DEC | Jointed rock, discrete blocks | File exchange | unspecified | **Awaiting a licence**; not verified |
| PC-Crash | Specialist collision analysis | File exchange | unspecified | **Blocked**: a documented export format to import is needed; imports are refused until then |

Seventeen native cross-checks across the six managed engines were run and all passed ([engine verification record](engine-verification.md)). Engines were verified on Ubuntu 24.04 x86_64 only.

---

## 7. Runs, results and metrics

### 7.1 Run lifecycle

States: `draft`, `validating`, `queued`, `preparing`, `running`, `awaiting_external_result`, `collecting`, `postprocessing`, `completed`, `failed`, `cancelled`.
Every transition is stored with its time and cause.

```
draft -> validating -> queued -> preparing -> running -> collecting -> postprocessing -> completed
              |                       |   \-> awaiting_external_result -> collecting
              v                       v
           failed                  queued (heartbeat lost, retries left)      any active state -> failed | cancelled
```

| ID | Requirement | Status |
| --- | --- | --- |
| R-1 | **Submission** checks access, the adoption gate for AI content, engine availability (refused with the engine's real state and a next step), the organisation's concurrency limit (default 8) and monthly quota (default 5000) with HTTP 429, and an idempotency key so a retried request never creates a second run | Verified |
| R-2 | **Validation** checks the scenario against its model and the engine's capability record before any work is queued; an invalid scenario fails in `validating` and never executes | Verified |
| R-3 | A run **freezes** (publishes) the exact scenario and model revisions it uses and records their hashes | Verified |
| R-4 | **Heartbeats and recovery:** workers send heartbeats (timeout 120 s by default); a recovery sweep re-queues a run whose worker died (up to 2 attempts by default) and otherwise fails it with a recorded reason; tasks are acknowledged late | Verified |
| R-5 | **Cancellation** terminates the engine's process group and discards partial outputs | Verified |
| R-6 | Long work runs in Celery tasks on the queues `cpu`, `gpu`, `reconstruction`, `results`, `maintenance`; the API dispatches by task name and never imports worker code | Verified |
| R-7 | Progress is available by polling and as server-sent events | Verified |

### 7.2 Results

Each run produces a **result envelope**: revisions, engine and adapter versions, execution mode, seed, solver settings, environment, **SHA-256 of every input and output**, metrics, references to trajectories, time series and fields, the validation report, warnings, native cross-checks (with tolerances and pass or fail), the processing history, and, for imported results, the external installation and who supplied the package.

Normalisation: **collect** native outputs unchanged as artifacts, **normalise** them into registered metrics in SI units and the canonical frame, **cross-check** key values recomputed from the native outputs, and **derive** display artifacts (for example playback sampled about every 0.5 s, for display only; metrics always use the originals).

| Metric status | Meaning |
| --- | --- |
| `available` | Computed from engine outputs |
| `unattained` | The engine did not reach it within the horizon (for example T100 when not everyone evacuated): never estimated |
| `unavailable` | The engine could not provide it |
| `not_applicable` | Does not apply to this run |

Replicated stochastic runs report each replicate and the distribution. A large result set is stored as artifacts and downloaded through signed URLs.

### 7.3 Metric catalogue

The registry has **72 metrics in 13 disciplines**, each with definition, calculation, source outputs, extent and interval, and what is reported when missing ([metrics.md](metrics.md)). The identifiers are listed in Appendix A.

---

## 8. Comparison, optimisation and reports

### 8.1 Comparison

A comparison of baseline and candidate runs first checks that they differ **only in their scenario changes**: engine and adapter versions, horizon, initial conditions, population or demand, seeds, boundary conditions and model revision. It then reports, per metric and entity, the absolute and where meaningful the percentage difference, with an explicit note when either is withheld (unavailable values, a zero baseline, incompatible units, interval-scale units). It guards specific misreadings: **a lower mean travel time that only reflects fewer completed trips is flagged as not an improvement**. Comparisons export to CSV. Status: Verified.

### 8.2 Optimisation

An optimisation study searches a declared parameter grid or coordinate path with **real runs**, common random numbers and an evaluation budget, and reports the **best tested candidate, never "the optimum"**. Adopting a candidate creates a normal scenario revision. Status: Verified.

### 8.3 Reports

A report is built from **one manifest** and rendered as a PDF (charts with a table twin for every chart; the supplied logo unaltered), a metrics CSV, and the JSON manifest, so the three always agree. The manifest holds the project, author, generation time and review status; each run (revisions with numbers and hashes, versions, seed, execution mode, metrics with statuses, cross-checks, warnings, model change notes); the comparison and its compatibility checks; a provenance summary (counts by kind, notable inputs with uncertainty, assumptions, whether synthetic material is present, any unadopted AI suggestion); reviews; and, for CrashLens case reports, evidence with hashes and handling, observations, interpretations with the observations they rely on, hypotheses with evidence links and analyses, and the statement that **the report does not determine fault**. Narrative is written by a person or is an adopted AI draft, and the report records which. Status: Verified.

---

## 9. The five modules

### 9.1 Mine Evacuation

| | |
| --- | --- |
| **Engine** | JuPedSim 1.4.2 (managed). Ventsim, FDS, FLAC3D, 3DEC cover ventilation, fire and ground studies by file exchange (awaiting environment) |
| **Inputs** | A prepared pedestrian geometry: walkable polygon, excluded areas, exits, start groups |
| **Controls** | Closed route segments, population overrides, pre-movement distribution, horizon; layout alternatives are separate model revisions (a proposed escapeway is a hypothetical object) |
| **Results** | 12 `evac.*` metrics: initial population, evacuated, unreachable, remaining, T50, T95, T100, mean and 95th percentile travel time, exit usage, peak density near exit, slow-movement fraction |
| **Guards** | Accounting identity (initial = evacuated + unreachable + remaining); T100 is `unattained` if anyone remains; trajectories are checked against the prepared geometry (5 cm tolerance) |
| **Limits** | One approximately level section; multi-level transfers, timed closures and dynamic re-routing are not modelled; timed closures are refused |
| **Status** | Verified |

### 9.2 CrashLens

| | |
| --- | --- |
| **Purpose** | Which hypotheses about a collision does the evidence support, and how closely? |
| **Evidence** | Originals hashed (SHA-256) at receipt and protected by **database triggers**; redactions, enhancements and annotations are separate derivatives with their own hashes and source links; every access is recorded in a handling log; capture times are shown as stated, unverified. A hash shows a file is unchanged since receipt, **not** that its content or timestamp is authentic |
| **Statements** | Observations record what evidence shows; interpretations record reasoning and **must cite the observations they rely on** |
| **Hypotheses** | Linked to evidence as supporting, contradicting or context; compared by those links and by residuals from collision-engine analyses, **never by probability**. Until an analysis is imported, evidence fit is shown as "not evaluated" |
| **Calculation** | Built-in skid-to-stop speed range, `v = sqrt(2 x mu x g x d)` for the low and high friction bounds (`crash.skid_speed_low`, `crash.skid_speed_high`); imported analyses give `crash.rms_residual` and `crash.targets_within_uncertainty` |
| **Engines** | Virtual CRASH (file exchange; awaiting a licence); PC-Crash (blocked) |
| **Access** | Cases are visible only to assigned investigators and reviewers; case-scoped operations enforce assignment (`require_case`, `require_scoped`) |
| **Reports** | Case report with evidence identifiers, hashes, references, and the statement that fault is not determined |
| **Status** | Verified; collision engines awaiting environment |

### 9.3 Construction Sites

| | |
| --- | --- |
| **Engines** | SimPy 4.1.2 (operations) and JuPedSim (evacuation drill derived from the same plan scene) are verified. Isaac Sim, FLAC3D, 3DEC pathways as in section 6.4. OpenFOAM runs approved two-dimensional site-wind templates only |
| **Workflows** | Site evacuation; delivery and resource scheduling (replications with common random numbers); vehicle and equipment movement; excavation and ground response; configured environmental studies |
| **Results** | 8 `ops.*` metrics (completed, unfinished, mean and 95th percentile wait, utilisation, maximum and mean queue, mean cycle time) with replicate statistics; every request is granted, waiting or unfinished, cross-checked against the event log |
| **Status** | Verified |

### 9.4 Urban Planning

| | |
| --- | --- |
| **Engines** | SUMO 1.28.0 (verified); JuPedSim; SWMM; OpenFOAM; EnergyPlus with OpenStudio (preparation verified, execution awaiting) |
| **Inputs** | Road network and junctions; demand is **supplied, never inferred** from imagery or road width |
| **Controls** | Closed edges, lane and speed changes, junction control types, signal programs, demand scale, horizon, warm-up |
| **Results** | 12 `traffic.*` metrics including scheduled, completed, unfinished and not-inserted trips, completion ratio, mean and 95th percentile travel time, mean time loss, throughput, queue length, teleports |
| **Guard** | Mean travel time is read with the completion ratio; a lower mean that coincides with fewer completed trips is flagged as not an improvement. SUMO's own trip statistics are cross-checked |
| **Status** | Verified; energy execution awaiting environment |

### 9.5 Urban Drainage

| | |
| --- | --- |
| **Engines** | SWMM 5.2.4 (verified); EPANET 2.2 for pressurised water networks (verified); HEC-RAS 6.6 for two-dimensional surface flooding by file exchange (awaiting environment) |
| **Inputs** | Drainage network (nodes, conduits, outfall, catchments), rainfall series (its hash is recorded and identical across compared runs), and a vertical reference |
| **Controls** | Rainfall series and scale, conduit blockages and resizes, storage conversions, low-impact development controls, pump and catchment changes |
| **Results** | 13 `drainage.*` metrics including rainfall, runoff and flooding volumes, node flooding and surcharge durations, peak outfall flow, conduit fill, storage depth, and continuity errors; 6 `water.*` metrics for EPANET |
| **Guards** | Continuity errors are reported as metrics; volumes are cross-checked against SWMM's own report; disconnected nodes are refused |
| **Limits** | **SWMM node flooding is not a surface flood map**; a two-dimensional model and terrain are needed (HEC-RAS by file exchange) and Parallel does not animate a flood from SWMM |
| **Status** | Verified; HEC-RAS awaiting environment |

### 9.6 Demonstration package

Five demonstration projects, one per module, are built from **real recorded runs** and every one is labelled synthetic (`python -m parallel_api.demo seed|status|reset`; `reset` only touches projects marked as demonstrations).
They are: *Level 3 mine section: evacuation*; *Training junction collision* (CrashLens); *Riverside block: site logistics*; *Crossroads corridor: junction control*; *Mill Lane catchment: flooding*. Presenter notes, a fifteen-minute running order and "things not to claim" are in the [demonstration guide](demo-guide.md). Status: Verified.

---

## 10. The AI assistant

The assistant is **optional**; every modelling and simulation workflow works without it.

| ID | Requirement | Status |
| --- | --- | --- |
| AI-1 | **Provider independence.** One interface, with Anthropic (default model `claude-opus-5-5`) and Gemini (default `gemini-2.5-pro`) implementations, a scripted provider for tests, and a disabled provider when no credentials are configured or the organisation forbids external AI. Selected by `PARALLEL_ASSISTANT_PROVIDER` ([assistant-providers.md](assistant-providers.md)) | Verified against stand-ins; **neither provider has been run against its live service** |
| AI-2 | **Acts only through server-side tools**, each authorised exactly as the same action would be for the signed-in user: inspect the project and scene revision, list domain models, get engine capabilities, identify missing inputs, get the scenario schema, create and validate scenario drafts, estimate run resources, submit runs, get run status and results, compare runs, draft reports, list case evidence (15 tools) | Verified |
| AI-3 | **Suggestions need adoption.** Scenario drafts and report narratives are marked `ai_suggested`, take effect only when a person adopts them, and a run refuses unadopted AI content | Verified |
| AI-4 | **Numerical grounding.** Every number it reports cites the metric identifier and run identifier from a tool result; unavailable or unattained values are said to be so; it never estimates a metric | Verified (scripted) |
| AI-5 | It never infers rock strength from appearance, hidden drainage geometry from surface photographs, vehicle speed from aftermath images, traffic demand from road width, or fire properties from colour; in CrashLens it keeps observations, interpretations and hypotheses distinct and never gives probabilities or fault | Enforced by the system prompt and tool design; verified by the scripted tests that check tool behaviour |
| AI-6 | **Data policy.** Organisation settings: *Allow project data to be sent to the configured language-model provider* (on by default) and *Allow CrashLens case records to be sent* (off by default); media files are never sent | Verified |
| AI-7 | Each message records the provider, the model that served it and token usage; provider errors and refusals are shown, not hidden; a bounded number of tool rounds (default 8) | Verified |
| AI-8 | AI scene assistance beyond "missing model information" (suggested object labels, candidate zones, exits, evidence tags, candidate geometry) | **Not implemented** |

---

## 11. Data specification

### 11.1 Core records

| Group | Entities |
| --- | --- |
| Identity and access | Organisation, user, organisation membership, project, project membership, case assignment |
| Captures | Capture batch, source asset (kind, filename, size, SHA-256, storage key, inspection, sensitivity, derived-from), reconstruction job (stages, settings, heartbeat) |
| Scenes | Scene, scene revision (JSON content, SHA-256, status, parent), spatial object, annotation, reference measurement |
| Models and scenarios | Domain model and revision, scenario and revision |
| Runs | Simulation run (states, attempts, worker, heartbeats), run artifact, run metrics, comparison, optimisation study and evaluations |
| Reviews and reports | Review, report |
| Engines | Engine capability record, installation, worker engine report |
| CrashLens | Case, evidence item and derivative, handling log, observation, interpretation, vehicle, timeline entry, hypothesis, evidence link, analysis |
| Assistant | Session, message, tool call (arguments, result, authorised, adoption) |
| Governance | Audit event (actor, object, project, case, details) |

Schema changes are Alembic migrations (`packages/core/migrations`, revisions 0001 to 0004); a test checks the models and migrations agree and that migrations downgrade and upgrade cleanly.

### 11.2 Revision rules

- Published revisions are **immutable**; an edit creates a new revision with its own number and SHA-256 and a link to its parent.
- A run freezes the revisions it uses; a report references revisions by number and hash.
- A draft may be edited until published.

### 11.3 Storage rules

- Originals are stored once and never overwritten; every object has a SHA-256; signed URLs for download.
- Backends: a local directory or volume, or any S3-compatible store (`PARALLEL_STORAGE_BACKEND`). **The S3 backend is tested against an emulator only.** Local storage must be a volume shared by the API and every worker.
- Large time series and trajectories are stored as Parquet artifacts; browser playback uses derived, sampled files.
- Evidence originals are protected from modification by database triggers.

### 11.4 Contracts

Pydantic v2 contracts (`packages/contracts`) are the only shapes that cross component boundaries; each top-level contract carries `schema_version: "1.0"`.
The **provenance** kinds are: `measured`, `imported`, `assumption`, `algorithm_estimate`, `ai_suggested` (with `adopted_by` and `adopted_at`), `derived` and `synthetic`; a provenance also records source, source reference, who and when, optional uncertainty (absolute, relative, range or standard deviation), and a note. Details: [data-contracts.md](data-contracts.md); live schemas: `GET /api/v1/models/schemas`.

---

## 12. Architecture and technology

### 12.1 Components

```
 Browser (apps/web) --HTTPS--> [Caddy] --> nginx (static app + /api proxy) --> API (services/api)
                                                                                 |        |
                                                                    PostgreSQL+PostGIS   Redis (queue, events)
                                                                                 |        |
                                          Object storage (local volume or S3) <--+        +--> workers (Celery)
                                                                                              |- simulation (queues cpu, maintenance; gpu on a GPU host)
                                                                                              |- reconstruction (queue reconstruction)
                                                                                              |- results (queue results)
                                                                                              '- scheduler (celery beat)
```

| Component | Code | Responsibility |
| --- | --- | --- |
| Browser application | `apps/web` | The interface; talks only to the API |
| API | `services/api` | Authentication, authorisation, validation, records, signed URLs, task dispatch, server-sent events; **never runs engines** |
| Simulation worker | `workers/simulation` | Runs managed engines through adapters, imports operator results, runs optimisation studies, recovers lost runs, reports engine availability |
| Reconstruction worker | `workers/reconstruction` | Capture processing through scene publication |
| Results worker | `workers/results` | Reports (PDF, CSV, JSON) |
| Scheduler | `celery beat` | Recovery sweep every minute; engine availability refresh every five minutes |
| Libraries | `packages/{contracts,scene,metrics,engines,core}` | Contracts, geometry, metrics, adapters, persistence, access, jobs, storage, backups |

### 12.2 Technology (as built, against the recommended stack)

| Layer | Used | Note |
| --- | --- | --- |
| Frontend | React 19, TypeScript 6, Vite 8, React Router 7, TanStack Query 5 | |
| 3D | Three.js 0.186, Spark 2.3 (Gaussian splats) | |
| Maps | **Not used** | The recommended MapLibre map is not built |
| API | FastAPI 0.161, Pydantic 2.14, Uvicorn | Python 3.13 (3.12 supported and used in the worker image) |
| Database | PostgreSQL 16 with PostGIS 3.5; SQLAlchemy 2.1, Alembic, GeoAlchemy2, psycopg 3 | |
| Queue | Celery 5.6 with Redis 7.4 | |
| Storage | Local volume, or S3 through boto3 | |
| Media and reconstruction | FFmpeg, COLMAP, OpenCV (headless), SciPy, plyfile; nerfstudio Splatfacto on a GPU worker | Open3D optional |
| Geometry and geospatial | NumPy, Shapely, PyProj, Rasterio, PyArrow | GDAL is reached through Rasterio |
| Reports | ReportLab, PyArrow | |
| Identity | Local accounts; OIDC token verification | **Keycloak is not used** |
| Packaging | Docker images and Docker Compose; Caddy for HTTPS | |
| Tests | pytest, Playwright, Vitest | |

### 12.3 Repository layout

`apps/web`, `services/api`, `workers/{simulation,reconstruction,results}`, `packages/{contracts,scene,metrics,engines,core}`, `infra/docker`, `compose.yaml`, `compose.https.yaml`, `scripts/` (engine matrix, reference docs, engine verification, benchmarks, runtimes, training notebook), `docs/`, `brand/`.
Dependencies are locked (`uv.lock`, `package-lock.json`); each engine has its own pinned runtime.

---

## 13. API specification

### 13.1 Conventions

- Base path `/api/v1`. Apart from sign-in, configuration and health, every endpoint needs `Authorization: Bearer <token>`.
- **Errors** have one shape: `{"error": {"code": "...", "message": "...", "details": ...}}`.
- List endpoints page with `limit` and `offset` and return `items`, `total`, `limit`, `offset`.
- Long work (reconstruction, runs, reports, optimisation) is **asynchronous**: the create call returns a job record; progress is by polling or server-sent events on `/events` endpoints.
- Run submission accepts an idempotency key. Quotas and concurrency limits answer 429.
- Interactive documentation at `/api/docs`; the live OpenAPI document at `/api/v1/openapi.json`; a committed copy at [`docs/api/openapi.json`](api/openapi.json).
- `GET /api/v1/health` reports API and database health; `GET /api/v1/metrics` exposes the metric registry.

### 13.2 Areas

**141 operations in 18 areas** ([api.md](api.md) lists every one):

| Area | Operations | Covers |
| --- | --- | --- |
| Assets | 6 | Capture batches, uploads, asset records, signed URLs |
| Assistant | 6 | Status, sessions, messages, tool-call adoption |
| Auth | 3 | Sign-in, current user, configuration |
| Cases | 38 | CrashLens cases, assignment, evidence, derivatives, handling, observations, interpretations, hypotheses, analyses, timeline, case reports |
| Comparisons | 4 | Create, read, export |
| Engines | 3 | Engine library, capability records, availability check |
| External-training | 3 | Training package, notebook, import of a trained splat |
| Meta | 2 | Health, metric registry |
| Models | 9 | Domain models and revisions, schemas, validation, publication |
| Optimisations | 5 | Studies, evaluations, adoption |
| Organisations | 8 | Organisations, members, settings, registered engine installations |
| Projects | 10 | Projects, members, templates, overview, activity, archiving |
| Reconstructions | 8 | Jobs, stages, frames, events, cancel and resume |
| Reports | 5 | Reports and downloads |
| Reviews | 2 | Scoped reviews |
| Runs | 11 | Submit, state, events, artifacts, metrics, exchange packages, external results, cancel |
| Scenarios | 8 | Scenarios, revisions, validation, adoption of AI-suggested revisions |
| Scenes | 10 | Scenes, revisions, publication, alignment, measurement evaluation, objects in a region, deriving a model |

Every consequential change writes an audit event (`parallel_core.audit.record`).

---

## 14. Non-functional requirements

### 14.1 Security

| Concern | Measure |
| --- | --- |
| Identity | scrypt password hashes; signed JWT sessions; OIDC token verification |
| Authorisation | Section 2; checked on the server for every request, including for the open-access guest (off by default) |
| Audit | Actor, object, project and case on every consequential change |
| Uploads | Size limits enforced while streaming; SHA-256 at receipt; content-signature checks; originals stored once |
| Archives | Traversal, absolute-path, symlink, member-size, total-size and compression-ratio checks (total limit 8 GiB by default) |
| Engines | Controlled templates only; isolated runtimes with a minimal environment and resource limits; unprivileged user in containers; uploaded code never executed |
| Downloads | Short-lived signed URLs (15 minutes by default) |
| Secrets | Read from the environment, never stored in the database; production refuses to start with a weak or default signing key (fewer than 32 characters) |
| Web | Security headers on every route (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, `Permissions-Policy`) |
| Backups | Database and objects together with a hash manifest; restoration refuses to overwrite existing data |

**Gaps:** retention policies and permanent-deletion workflows are not implemented; there is no rate limiting beyond run quotas; no formal penetration test was performed.

### 14.2 Privacy and external AI

External AI is governed by the organisation settings in AI-6. Provider data terms differ by account type (for example a free Gemini key may allow the provider to use prompts to improve its products); the operator is responsible for choosing a key whose terms fit the data. CrashLens media files are never sent to any provider.

### 14.3 Reliability and recovery

Late acknowledgement and heartbeats re-deliver a lost worker's task; the recovery sweep bounds retries; reconstruction resumes from checkpoints; backups are verifiable; migrations run as a one-shot service before the API starts. There is **one deployment unit** (no high availability) in the reference setup.

### 14.4 Performance (measured workloads, one host; not promised figures)

Recorded 2026-10-23 on a 4-CPU Xeon with 15.7 GB and no GPU ([benchmarks.md](benchmarks.md)):

| Workload | End to end |
| --- | --- |
| JuPedSim, 100 agents, level mine section | 1.72 s |
| JuPedSim, 160 agents | 3.76 s |
| SUMO, priority junction corridor, 1 h demand | 4.69 s |
| SWMM, 4 junctions, 60 min design storm | 0.71 s |
| SimPy, site deliveries, 10 replications | 0.5 s |
| Report (PDF, CSV, JSON) of a three-run comparison | 0.77 s |
| Reconstruction, 36 frames (synthetic), CPU | 24.5 s (20.8 s in COLMAP) |
| Reconstruction, 72 frames | 70.2 s |

The viewer plays a run in 1.7 s on software WebGL. Frame rates on machines without a GPU are those of software rendering.

### 14.5 Accessibility, observability and cost

- **Accessibility:** section 3.3, U-5.
- **Observability:** run records hold queue, start and finish times, attempts, worker, engine and adapter versions, per-state durations and artifact sizes; reconstruction jobs hold per-stage timings and logs; assistant messages hold usage. **Not built:** per-run CPU and memory use.
- **Cost controls:** per-run resource estimates, upload and archive limits, organisation concurrency and monthly quotas, optimisation evaluation budgets. **Not built:** GPU time tracking and storage use per organisation.

### 14.6 Portability and maintainability

Container images for Linux x86_64; engines verified on Ubuntu 24.04. Engine runtimes are isolated and pinned. Code is typed (Pydantic, SQLAlchemy typed models, TypeScript), linted (`ruff`, line length 110) and tested; comments explain why, not what.

---

## 15. Deployment and operations

### 15.1 Environments

| Environment | How |
| --- | --- |
| Development | `uv sync`, `scripts/setup_runtimes.sh`, services run directly, Vite dev server ([README](../README.md)) |
| Single host | `docker compose up -d --build`, with `compose.https.yaml` for automatic HTTPS (Caddy) ([deployment.md](deployment.md)) |
| Windows PC | The same stack in Docker Desktop ([setup-windows.md](setup-windows.md)) |
| Cloud virtual machine | The reference deployment ([google-cloud.md](google-cloud.md)) |
| Hosted and distributed | Managed PostgreSQL, S3-compatible storage, additional workers, a GPU worker, licensed installations (documented; **awaiting environment verification**) |

### 15.2 Container images

| Image | Base | Contents |
| --- | --- | --- |
| `parallel-app` | `python:3.13-slim` | API, results worker, scheduler, migrations, admin commands, FFmpeg and ffprobe |
| `parallel-worker` | `ubuntu:24.04` with Python 3.12 | Simulation and reconstruction workers; JuPedSim, SUMO, SWMM, EPANET, SimPy, OpenFOAM, OpenStudio, COLMAP, FFmpeg, the PostgreSQL 16 client. **No CUDA and no nerfstudio** |
| `parallel-web` | nginx | The built application; security headers; proxies `/api` with long timeouts |

The compose stack: `db` (PostGIS 16), `redis`, `migrate` (one shot), `api`, `results`, `scheduler`, `sim`, `recon`, `web`, and `caddy` with the HTTPS override.
Containers restart automatically (`unless-stopped`).

### 15.3 Configuration

Every setting is a `PARALLEL_*` environment variable documented in [`.env.example`](../.env.example) and `settings.py`. The ones that matter in production:

| Setting | Default | Purpose |
| --- | --- | --- |
| `PARALLEL_ENVIRONMENT` | `development` | `production` refuses a weak signing key |
| `PARALLEL_SECRET_KEY` | (insecure placeholder) | Signs sessions and download URLs; at least 32 random characters |
| `PARALLEL_PUBLIC_BASE_URL`, `PARALLEL_WEB_ORIGIN` | localhost | Signed URL host and allowed browser origin |
| `PARALLEL_DATABASE_URL`, `PARALLEL_REDIS_URL` | local | Connections |
| `PARALLEL_STORAGE_BACKEND` | `local` | `local` or `s3` (with endpoint, bucket, region, keys) |
| `PARALLEL_MAX_UPLOAD_BYTES`, `PARALLEL_MAX_ARCHIVE_TOTAL_BYTES` | 4 GiB, 8 GiB | Limits |
| `PARALLEL_ORG_CONCURRENT_RUN_LIMIT`, `PARALLEL_ORG_MONTHLY_RUN_QUOTA` | 8, 5000 | Per-organisation run limits |
| `PARALLEL_HEARTBEAT_TIMEOUT_S`, `PARALLEL_MAX_RUN_ATTEMPTS` | 120, 2 | Recovery |
| `PARALLEL_ASSISTANT_PROVIDER`, `PARALLEL_ANTHROPIC_API_KEY`, `PARALLEL_GEMINI_API_KEY`, `PARALLEL_GEMINI_MODEL` | `anthropic`, none | The optional assistant |
| `PARALLEL_AUTH_MODE` and `PARALLEL_OIDC_*` | `local` | Identity |

### 15.4 Backups, upgrades and operations

- **Backup and restore:** `python -m parallel_core.backup create|verify|restore` backs up the database and every stored object together with a manifest (schema revision, row counts, size and SHA-256 of every object and archive); a restore refuses to overwrite existing data and verification re-checks every hash. Tested, including restoration into a second empty deployment.
- **Upgrade:** pull the code, rebuild, and the one-shot migration service upgrades the schema before the API starts.
- **Health:** `GET /api/v1/health`; container health checks use it.
- **Logs:** each service logs to standard output; run, reconstruction and engine logs are stored as artifacts.

### 15.5 Reference deployment (28 October 2026)

One Google Cloud VM (`e2-standard-4`: 4 vCPU, 16 GB, no GPU; Ubuntu 24.04; 100 GB disk; reserved static IP; firewall open on ports 80 and 443), running the compose stack with Caddy for HTTPS under a DuckDNS hostname. **There are no scheduled backups yet, no uptime monitor, and no GPU.** Defects found and fixed while setting it up are listed in section 16.4.

---

## 16. Verification

### 16.1 Strategy

**A skipped test is not a pass, and nothing is mocked that the claim depends on.** Engine tests run the pinned engines and fail when a runtime is missing; API tests use a real PostgreSQL and PostGIS database built by the migrations, real storage, and real Celery dispatch through Redis; reconstruction tests run COLMAP; browser tests drive Chromium against a running deployment. (The assistant's providers are tested against stand-ins, because no live key was available.)

### 16.2 Test inventory

| Area | Tests | What they establish |
| --- | --- | --- |
| Contracts | 20 | Units, geometry rules, provenance and adoption rules, run-state transitions, discipline matching |
| Scene | 27 | Transforms, similarity fitting, georeference, polygon validity, connectivity, PLY reading, measurement evaluation |
| Metrics | 17 | Registry integrity; evacuation, operations and collision metrics; comparison rules |
| Engines | 36 | Real reference workflows; file-exchange packaging and import; safe archive extraction; every engine has an adapter and complete capability record |
| Core | 8 | Migrations equal the models; S3 storage against an emulator; restore filtering |
| API | 78 | Authentication, open access mode, isolation, roles; uploads; revisions; run lifecycle with real engines; honest refusals; file exchange; quotas; reconstruction; external training and splat import; comparisons, reviews, reports; CrashLens; optimisation; the assistant including Gemini; demonstrations; backup and restore; the admin command |
| Simulation worker | 3 | Dispatch, cancellation killing the engine, recovery of a lost heartbeat |
| Results worker | 5 | Chart scales, table twins, logo byte-identity |
| **Python total** | **194** | |
| Browser (Playwright) | 18 | Workflow end to end, access boundaries, external-training panel, accessibility in both colour schemes; any console error fails the test |

### 16.3 Engine verification

Seventeen native cross-checks across JuPedSim, SUMO, SWMM, EPANET, SimPy and OpenFOAM, each with a stated tolerance and rationale, all passing ([engine-verification.md](engine-verification.md), regenerated by `scripts/engine_verification.py`).

### 16.4 Other recorded checks

Container build and full-stack run with the five demonstrations seeded; backup and restore across deployments with every hash re-verified; access boundaries; and, on the reference VM, sign-in, video upload, reconstruction of a phone video (140 sparse points, 0.34 px mean reprojection error), and HTTPS certificate issuance. Defects found by this verification and fixed include file modes in images, nginx header handling, the API refusing runs its own host could not execute, a response-before-commit race (fixed by committing before the response), line endings on Windows checkouts, and missing `ffprobe` in the API image.

### 16.5 Not verified

The training commands on a real GPU; the live assistant providers; every licensed engine and Isaac Sim; HEC-RAS and FDS execution; EnergyPlus execution; S3 storage against a real provider; tablet and phone layouts; any platform other than Ubuntu 24.04 x86_64 for engines; video input in an automated test; a formal accessibility audit.

---

## 17. Status against the development specification, limitations and roadmap

### 17.1 Status by area

The authoritative section-by-section table is [implementation-status.md](implementation-status.md). Summary:

| Development specification area | Status |
| --- | --- |
| 4 Foundational rules; 5 Users and access; 6 Navigation and project creation | Verified |
| 7 Interface | Scene workspace in progress; accessibility verified on desktop |
| 8 to 10 Architecture, stack, repository; 11 Data model; 12 Coordinates, quality states | Verified |
| 13 Capture and reconstruction | Verified on CPU; **GPU training awaiting environment**; external training built |
| 14 Scene preparation and editing | In progress |
| 15 Engine framework | Verified |
| 16 Engine catalogue | Six verified and running; the rest per section 6.4 |
| 17 to 21 The five modules | Verified (collision, energy, FDS and HEC-RAS pathways awaiting environment) |
| 22 AI capabilities | Reconstruction AI awaiting environment; scene assistance in progress; drafting, tools, grounding, provider independence verified with stand-ins |
| 23 Comparison and optimisation; 25 Jobs and results; 26 API; 27 Reports | Verified |
| 24 Multi-engine workflows | **Not implemented** |
| 28 Security, deployment, observability, cost | Security, observability and cost in progress; single-host deployment verified |
| 30 Testing; 31 Demonstration package | Verified |

### 17.2 Known limitations

- **Movement.** One approximately level section; no multi-level transfers, timed closures or dynamic re-routing.
- **Traffic.** Results depend on the demand supplied; it is never inferred.
- **Drainage.** SWMM node flooding is not a surface flood map.
- **CFD.** Approved two-dimensional site-wind templates only; uploaded cases are never run.
- **Collision analysis.** Parallel never estimates collision dynamics itself; it computes skid-to-stop ranges and residuals of imported analyses; it never gives hypothesis probabilities or determines fault.
- **Reconstruction.** Scale needs surveyed references; captured surfaces never establish hidden geometry; training needs a GPU; the viewer does not draw meshes, terrain or LAS point clouds.
- **Calibration.** None; the state is never set automatically.
- **Platforms.** Engines verified on Ubuntu 24.04 x86_64 only.

### 17.3 Roadmap (not scheduled)

1. **GPU training in the pipeline:** build a GPU worker image (CUDA and nerfstudio); **change the in-pipeline training stage to train on Parallel's own COLMAP model with orientation, centring and scale switched off and apply the import's alignment check** (it currently re-runs COLMAP inside nerfstudio, so its splat would be in the wrong frame); verify on a real GPU.
2. Verify the live assistant providers; add AI scene assistance (labels, candidate zones, exits, evidence tags).
3. Interactive geometry editing, undo and redo, network editing tools; drawing meshes, terrain and LAS point clouds; terrain-to-model alignment.
4. Licensed and operator-run engines against real installations; the Isaac Sim connection protocol; a documented PC-Crash exchange format.
5. Browser sign-in through an organisation's identity provider.
6. Retention policies and permanent-deletion workflows; per-run resource use; GPU and storage accounting.
7. Multi-engine workflows with explicit variable, unit, time and conservation definitions.
8. Operational maturity: scheduled backups and a restore rehearsal, uptime monitoring, continuous integration with branch protection, tablet and phone testing, a WCAG 2.2 AA audit.

---

## 18. Appendices

### Appendix A: metric identifiers (72 in 13 disciplines)

Definitions, calculations, source outputs, extents and missing-value rules are in [metrics.md](metrics.md).

| Discipline | Identifiers |
| --- | --- |
| Pedestrian (12) | `evac.initial_population`, `evac.evacuated`, `evac.unreachable`, `evac.remaining`, `evac.t50`, `evac.t95`, `evac.t100`, `evac.mean_travel_time`, `evac.p95_travel_time`, `evac.exit_usage`, `evac.peak_exit_density`, `evac.slow_movement_fraction` |
| Traffic (12) | `traffic.scheduled_trips`, `traffic.completed_trips`, `traffic.unfinished_trips`, `traffic.not_inserted`, `traffic.completion_ratio`, `traffic.mean_travel_time`, `traffic.p95_travel_time`, `traffic.mean_time_loss`, `traffic.throughput`, `traffic.max_queue_length`, `traffic.network_max_queue_length`, `traffic.teleports` |
| Drainage (13) | `drainage.total_rainfall_volume`, `drainage.total_runoff_volume`, `drainage.total_flooding_volume`, `drainage.node_flooding_volume`, `drainage.node_flood_duration`, `drainage.node_surcharge_duration`, `drainage.max_node_depth`, `drainage.peak_outfall_flow`, `drainage.conduit_max_fill`, `drainage.conduit_max_flow_ratio`, `drainage.max_storage_depth`, `drainage.runoff_continuity_error`, `drainage.routing_continuity_error` |
| Water supply (6) | `water.min_pressure`, `water.network_min_pressure`, `water.deficient_junctions`, `water.max_velocity`, `water.total_demand`, `water.tank_level_range` |
| Operations (8) | `ops.completed`, `ops.unfinished`, `ops.mean_wait`, `ops.p95_wait`, `ops.utilisation`, `ops.max_queue`, `ops.mean_queue`, `ops.mean_cycle_time` |
| CFD (4) | `cfd.max_speed`, `cfd.exceedance_area`, `cfd.final_residual_p`, `cfd.mesh_cells` |
| Collision (4) | `crash.rms_residual`, `crash.targets_within_uncertainty`, `crash.skid_speed_low`, `crash.skid_speed_high` |
| Fire (3) | `fire.peak_hrr`, `fire.device_max_temperature`, `fire.device_min_visibility` |
| Thermal (4) | `thermal.peak_heating_load`, `thermal.peak_cooling_load`, `thermal.heating_energy`, `thermal.cooling_energy` |
| Surface hydraulics (2) | `hyd.max_depth`, `hyd.inundated_area` |
| Rigid body (2) | `rigid.freefall_time_error`, `rigid.max_speed` |
| Ventilation (1) | `vent.total_airflow` |
| Geomechanics (1) | `geomech.max_displacement` |

### Appendix B: glossary

| Term | Meaning |
| --- | --- |
| Adapter | The code that prepares an engine's inputs, runs it, normalises its outputs and cross-checks them |
| Adoption | A person's explicit acceptance of an AI suggestion; until then it cannot be used by a run |
| Capture frame | The coordinate frame of a reconstruction before alignment |
| Canonical frame | The scene's working frame: Z up, metres |
| Cross-check | Recomputation of a normalised value from the engine's own native output, with a stated tolerance |
| File exchange | An engine run by a qualified person outside Parallel, whose result package is imported |
| Gaussian splat | A set of 3D Gaussians that renders a captured place photo-realistically; appearance only |
| Provenance | The recorded origin of a value: measured, imported, assumption, algorithm estimate, AI-suggested, derived or synthetic |
| Revision | An immutable, hashed version of a scene, model or scenario |
| Quality state | One of seven independently assessed claims about a scene's reliability |
| Unattained / unavailable | A value the engine did not reach / could not provide; never estimated |

### Appendix C: document index

| Document | Contents |
| --- | --- |
| [PARALLEL_DEVELOPMENT_SPEC.md](PARALLEL_DEVELOPMENT_SPEC.md) | The requirements the project was built to |
| [architecture.md](architecture.md) | Components, data flow, job lifecycle, security |
| [data-contracts.md](data-contracts.md) | Provenance, coordinates, scenes, models, scenarios, results, reports |
| [implementation-status.md](implementation-status.md) | Status of every specification area and known limitations |
| [engine-matrix.md](engine-matrix.md), [engine-verification.md](engine-verification.md) | Engines (generated) |
| [metrics.md](metrics.md), [api.md](api.md) | Metric catalogue and HTTP reference (generated) |
| [reconstruction.md](reconstruction.md), [external-training.md](external-training.md) | Capture, pipeline, and training a splat elsewhere |
| [assistant-providers.md](assistant-providers.md) | Claude or Gemini for the assistant |
| [demo-guide.md](demo-guide.md) | The five demonstrations |
| [validation.md](validation.md), [benchmarks.md](benchmarks.md) | Verification record and measured workloads |
| [deployment.md](deployment.md), [google-cloud.md](google-cloud.md), [setup-windows.md](setup-windows.md) | Running it |

### Appendix D: traceability to the development specification

| Development specification section | Where specified here |
| --- | --- |
| 2 to 3 Scope | 1 |
| 4 Foundational rules | 1.5 |
| 5 Users and access | 2 |
| 6 to 7 Application and interface | 3 |
| 8 to 10 Architecture, stack, repository | 12 |
| 11 to 12 Data, coordinates, quality | 5, 11 |
| 13 Capture and reconstruction | 4 |
| 14 Scene preparation | 5.3 |
| 15 to 16 Engines | 6 |
| 17 to 21 Modules | 9 |
| 22 AI | 10 |
| 23 Comparison and optimisation | 8 |
| 24 Multi-engine workflows | 1.4, 17.1 |
| 25 Jobs and results | 7 |
| 26 API | 13 |
| 27 Reports | 8.3 |
| 28 Non-functional | 14, 15 |
| 30 Testing | 16 |
| 31 Demonstration package | 9.6 |
| 33 Deliverables | [implementation-status.md](implementation-status.md) |
