# Implementation status

The state of every area of the [specification](PARALLEL_DEVELOPMENT_SPEC.md), using the categories
required by its section 33. Evidence for "verified" items is in [validation.md](validation.md).
Updated 2026-10-23 on branch `claude/gracious-allen-6xoln9`.

| Category | Meaning here |
| --- | --- |
| **Implemented and verified** | Built, and exercised by automated tests or a recorded end-to-end check on the platform named |
| **Implemented, awaiting environment verification** | Built and tested as far as this environment allows; needs an engine installation, licence, GPU or device that was not available |
| **In progress** | Partly built; what exists works, and what is missing is listed |
| **Blocked by a named dependency** | Cannot proceed until the named external item exists |
| **Not implemented** | Not started |

## Summary by area

| Spec | Area | Status | Notes |
| --- | --- | --- | --- |
| 4.1 | Visual reconstruction kept separate from simulation models | Implemented and verified | Captured, prepared and hypothetical content are distinct records and are drawn differently; derived models use only prepared objects, and leave proposals out unless asked |
| 4.2 | Provenance on every input | Implemented and verified | Seven provenance kinds; reports count them; unadopted AI content blocks runs |
| 4.3 | Engines produce numerical results | Implemented and verified | Metric registry enforced; unattained and unavailable values reported as such |
| 4.4 | Reproducibility | Implemented and verified | Immutable revisions with hashes; runs record revisions, input and output hashes, seeds, engine and adapter versions, environment |
| 5 | Users, roles and case access | Implemented and verified | Organisation, project and case roles; case access by explicit assignment only; reviews state their scope |
| 6.1 | Navigation | Implemented and verified | All global, project and CrashLens sections |
| 6.2 | Project creation | Implemented and verified | Name, type, description, location, georeference, membership, demonstration template |
| 7.1 | Visual direction | Implemented and verified | Supplied logo unaltered; dark default and light theme; reviewed in both |
| 7.2 | Scene workspace | In progress | Done: viewport, layers and objects panel, properties panel, playback timeline with side-by-side comparison, orbit, pan, zoom, top-down, fit, named viewpoints, layer visibility, selection, measurement with reference checks, CrashLens evidence markers, saved images, quality and measurement panels. Missing: placing new annotation markers from the toolbar, and the assistant opened in the context of a selected object (the assistant is available beside the workspace) |
| 7.3 | Captured, model and combined views; moved-object discrepancy | Implemented and verified | A moved object whose appearance is baked into the capture shows its captured position in red with a warning label |
| 7.4 | Accessibility | Implemented and verified (desktop); tablet and phone in progress | Keyboard navigation, focus indicators, skip link, single main landmark, status text with colour, table for every chart, reduced motion, non-3D access to all key information; checked by browser tests in both colour schemes. Responsive layouts exist but have not been tested on tablets or phones, and no formal WCAG 2.2 AA audit has been performed |
| 8 to 10 | Architecture, stack, repository | Implemented and verified | See [architecture](architecture.md) |
| 11 | Data model, revision and storage rules | Implemented and verified | Migrations tested against the models; evidence originals protected by database triggers |
| 12.1 to 12.3 | Coordinates, georeference, scene contract | Implemented and verified | Z-up metres; projected CRS required for metric work; explicit vertical reference |
| 12.4 | Domain schemas | Implemented and verified | Fourteen model kinds across thirteen disciplines |
| 12.5 | Quality states | Implemented and verified | Recorded on the revision; later states derived from the models, reviews and runs that cite it; calibration never inferred |
| 13.1 | Supported inputs | In progress | See [supported inputs](#supported-inputs) |
| 13.2 | Guided capture, offline upload | Implemented and verified | |
| 13.3 to 13.5 | Processing stages, outputs, measurement validation | Implemented and verified (CPU); training awaiting environment | All eleven stages with checkpoints and resume; scale from surveyed references with held-out checks; tolerances with their stated purpose. Gaussian training needs an NVIDIA GPU with nerfstudio and has not been run; without it an untrained initialisation is published and labelled |
| 13.6 | Generated content only in hypothetical scenes | Implemented and verified | Enforced by the scene contract; Parallel does not itself generate scene content |
| 14 | Scene preparation and editing | In progress | Done: object kinds (walkable areas, obstacles, exits, refuges, zones, routes, roads, junctions, conduits, vehicles), properties with provenance and belonging, revision creation, geometry validation before simulation (self-intersecting polygons, drainage, water and road network connectivity, required physical properties through the model schemas, and flags on non-specialist geomechanics properties), model import from files and editing as JSON, deriving a movement model from prepared objects. Missing: interactive drawing and editing of geometry in the 3D view, undo and redo, network editing tools, and export of scenes to other formats |
| 15 | Engine integration framework | Implemented and verified | Common adapter interface, capability records, three execution modes, availability from real checks (worker-reported for managed engines) |
| 16 | Complete engine catalogue | See [engines](#engines) | |
| 17 | Mine Evacuation | Implemented and verified | Single approximately level section, as the specification allows; timed closures refused until implemented |
| 18 | CrashLens | Implemented and verified; collision engines awaiting environment | Evidence integrity, observations and interpretations, hypotheses, playback of imported analyses, case reports. Virtual CRASH import implemented and tested against its exchange format; no licensed installation was available |
| 19 | Construction Sites | Implemented and verified | SimPy operations and JuPedSim evacuation; Isaac Sim, FLAC3D, 3DEC and OpenFOAM pathways as in the engine table |
| 20 | Urban Planning | Implemented and verified | SUMO with the unfinished-trip guard; energy (EnergyPlus) awaiting environment |
| 21 | Urban Drainage | Implemented and verified | SWMM with continuity and volume checks; no surface flood map from SWMM; HEC-RAS by file exchange awaiting environment |
| 22.1 | Reconstruction AI | Implemented, awaiting environment verification | Splatfacto training path built; no GPU available |
| 22.2 | AI scene assistance | In progress | Done: missing model information. Missing: suggested object labels, candidate zones, exits, evidence tags and candidate geometry |
| 22.3 to 22.6 | Scenario drafting, allowed tools, numerical grounding, provider independence | Implemented and verified (with a scripted provider) | Not exercised against a live model: no API key on the verification host |
| 23 | Comparison and optimisation | Implemented and verified | Controlled comparisons with compatibility checks; grid and coordinate search reporting the best tested candidate |
| 24 | Multi-engine workflows | Not implemented | Independent studies sharing a scene work today and are never presented as coupled. One-way exchanges, iterative coupling and co-simulation, with their variable, unit, time and conservation definitions, are not built |
| 25 | Job execution and results | Implemented and verified | States, heartbeats, cancellation, timeouts, bounded retries, recovery sweep, result envelopes, large-result playback derivatives, metric definitions |
| 26 | API | Implemented and verified | 138 operations; [reference](api.md) and [OpenAPI document](api/openapi.json) |
| 27 | Reports and export | Implemented and verified | PDF, CSV and JSON from one manifest; comparison CSV export; case reports |
| 28.1 | Security | In progress | Done: isolation, case access, server-side authorisation, audit, secrets from the environment, signed URLs, safe parsing, archive validation, restricted engine processes, backups, project archiving. Missing: retention policies and permanent-deletion workflows |
| 28.2 | AI data access | Implemented and verified | Organisation policy for external AI and for case records; media never sent |
| 28.3 | Deployment | Implemented and verified (single host); GPU and hosted awaiting environment | Container images and compose verified end to end; S3 storage tested against an emulator only |
| 28.4 | Observability | In progress | Done: job and queue times, failures, engine versions, reconstruction stage timings, output sizes, AI usage. Missing: worker resource use (CPU and memory per run) |
| 28.5 | Cost controls | In progress | Done: per-run estimates, job size and upload limits, organisation concurrency limits and monthly quotas, optimisation budgets. Missing: GPU time tracking and storage use per organisation |
| 30 | Testing and acceptance | Implemented and verified | 158 automated tests, 17 browser tests, engine verification record, benchmarks |
| 31 | Demonstration package | Implemented and verified | Five projects from real recorded runs, labelled saved results, presenter notes, safe reset |
| — | Sign-in with an organisation's identity provider (OIDC) | In progress | The API verifies OIDC tokens against the provider's keys; there is no browser sign-in flow, no account linking and no test |

## Engines

Details, tested versions and limitations are in the [engine matrix](engine-matrix.md); cross-check
records are in the [engine verification record](engine-verification.md).

| Engine | Mode(s) | Status |
| --- | --- | --- |
| JuPedSim 1.4.2 | Managed | Implemented and verified |
| Eclipse SUMO 1.28.0 | Managed | Implemented and verified |
| EPA SWMM 5.2.4 | Managed | Implemented and verified |
| EPA EPANET 2.2 (WNTR 1.5.0) | Managed | Implemented and verified |
| SimPy 4.1.2 | Managed | Implemented and verified |
| OpenFOAM v1912 | Managed (approved templates) | Implemented and verified |
| EnergyPlus 25.2.0 with OpenStudio 3.11.0 | Managed, file exchange | OpenStudio preparation verified; EnergyPlus execution awaiting environment |
| NIST FDS 6.10.1 | Managed, file exchange | Implemented, awaiting environment verification |
| HEC-RAS 6.6 | File exchange | Implemented, awaiting environment verification |
| NVIDIA Isaac Sim 5.x | Managed on a GPU worker, connected | Implemented, awaiting environment verification (RTX GPU) |
| Virtual CRASH | File exchange | Implemented, awaiting environment verification (licence) |
| Ventsim | File exchange | Implemented, awaiting environment verification (licence); no installation tested |
| FLAC3D | File exchange | Implemented, awaiting environment verification (licence); no installation tested |
| 3DEC | File exchange | Implemented, awaiting environment verification (licence); no installation tested |
| PC-Crash | File exchange | Blocked by a named dependency: a documented PC-Crash export format to import (imports are refused until then) |
| Isaac Sim connected installations | Connected | In progress: the connection protocol for remote hosts is not implemented |

## Supported inputs

| Input | Formats | Status |
| --- | --- | --- |
| Photographs, drone stills | JPEG, PNG, TIFF | Implemented and verified (reconstruction tests) |
| Video, drone video | MP4, MOV, M4V, MKV, AVI (FFmpeg) | Implemented, awaiting verification: frame extraction is built but no automated test uses video input |
| Gaussian splats | PLY (Inria layout) verified; SPLAT, SPZ, KSPLAT accepted and passed to Spark by name | PLY implemented and verified; other formats implemented, not yet tested |
| Point clouds | PLY verified (drawn in the workspace); LAS and LAZ accepted and stored | PLY implemented and verified; LAS and LAZ display not implemented |
| Meshes | glTF, GLB, OBJ, STL accepted and stored | Display in the workspace not implemented |
| Terrain | GeoTIFF with a CRS (inspected with rasterio) | Stored and inspected; display and terrain-to-model alignment not implemented |
| Domain model files | Parallel model envelope (JSON); SWMM `.inp`, SUMO networks and routes, EPW weather accepted | Envelope import implemented and verified; native engine files are stored for reference, not converted |
| Result packages | ZIP per engine exchange format | Implemented and verified per engine (see engines) |

## Deliverables (section 33)

| # | Deliverable | Where |
| --- | --- | --- |
| 1 | Working source code | This repository |
| 2 | Reproducible development setup | [README](../README.md), `uv.lock`, `package-lock.json`, `scripts/setup_runtimes.sh` |
| 3 | Database migrations | `packages/core/migrations` |
| 4 | Environment variable example | [`.env.example`](../.env.example) |
| 5 | API documentation | [api.md](api.md), [openapi.json](api/openapi.json), `/api/docs` |
| 6 | Engine installation and capability checks | Engine Library page, `/api/v1/engines`, [engine matrix](engine-matrix.md) |
| 7 | Engine adapters and integration records | `packages/engines`, [engine verification record](engine-verification.md) |
| 8 | Five demonstration projects | `python -m parallel_api.demo`, [demo guide](demo-guide.md) |
| 9 | Reconstruction setup and example | [reconstruction.md](reconstruction.md) |
| 10 | Model and scenario schemas | `packages/contracts`, `/api/v1/models/schemas`, [data contracts](data-contracts.md) |
| 11 | Result normalisation documentation | [data contracts](data-contracts.md#normalisation), [metric catalogue](metrics.md) |
| 12 | Automated tests | 158 Python tests, 17 browser tests ([validation](validation.md)) |
| 13 | Benchmark results | [benchmarks.md](benchmarks.md) |
| 14 | Deployment instructions | [deployment.md](deployment.md), `compose.yaml`, `infra/docker` |
| 15 | Report templates | `workers/results` ([data contracts](data-contracts.md#reports)) |
| 16 | Demonstration reset tooling | `python -m parallel_api.demo reset` |
| 17 | Known limitations | Below, and per engine in the [engine matrix](engine-matrix.md) |
| 18 | Accurate implementation status | This document |

## Known limitations

- **Movement model scope.** JuPedSim runs one approximately level section with the
  CollisionFreeSpeedModel; multi-level transfers, timed closures and dynamic re-routing are not modelled.
- **Traffic.** SUMO results depend on the demand supplied; demand is never inferred from imagery or road
  width. Comparisons flag lower travel times that only reflect fewer completed trips.
- **Drainage.** SWMM node flooding is not a surface flood map; a two-dimensional model and terrain are
  needed, through HEC-RAS file exchange.
- **CFD.** OpenFOAM runs approved two-dimensional site-wind templates only; uploaded cases are never run.
- **Collision analysis.** Parallel computes skid-to-stop speed ranges and residuals of imported analyses
  against evidence targets; it never estimates collision dynamics itself, never gives probabilities
  that a hypothesis is true and never determines fault. A hash shows a file is unchanged since receipt,
  not that its content or timestamp is authentic.
- **Reconstruction.** Training needs a GPU; without one, scenes carry an untrained initialisation.
  Captured surfaces never establish hidden geometry (pipes, inverts, rock properties). Scale requires
  surveyed references; without them scale is recorded as unknown.
- **Calibration.** No module calibrates a model against observations; the quality state is never set
  automatically.
- **Assistant.** Optional; verified with a scripted provider; it acts only through server-side tools
  and its drafts take effect only when adopted.
- **Viewer.** Meshes, terrain and LAS point clouds are stored but not drawn; frame rates on machines
  without a GPU are those of software WebGL (see [benchmarks](benchmarks.md)).
- **Platforms.** Engines were verified on Ubuntu 24.04 (x86_64). Other platforms in each engine's
  capability record are declared by the engine's distribution and have not been tested here.
