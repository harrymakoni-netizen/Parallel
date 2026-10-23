# Validation

How Parallel is verified, what was verified, and on which platform. The rule throughout: **a skipped
test is not a pass, and nothing is mocked that the claim depends on.** Engine tests run the pinned
engines; API tests use a real PostgreSQL and PostGIS database built by the migrations, real storage and
real Celery dispatch through Redis; reconstruction tests run COLMAP; browser tests drive Chromium against
a running deployment.

Recorded on 2026-10-23 on a 4-CPU Intel Xeon host with 15.7 GB of memory and no GPU, Ubuntu 24.04,
Python 3.13.16 (and Python 3.12 inside the worker image).

## Running the checks

```bash
uv run pytest                                     # 158 tests
uv run ruff check .
uv run python scripts/engine_verification.py      # cross-check record for every managed engine
(cd apps/web && npm run typecheck && npm run build)
(cd apps/web && E2E_PASSWORD=... npm run e2e)     # 17 browser tests, against a running stack with demos seeded
```

Tests need PostgreSQL 16 with PostGIS (a `parallel_template` database holding only the extension) and
Redis; they create a disposable database per session and use Redis database 15, so they never share
queues with a development worker. Engine tests fail when a runtime is missing; on a machine that
deliberately lacks an engine, `PARALLEL_ALLOW_MISSING_ENGINES=1` turns that into an explicit, reported
skip.

## Test inventory

| Area | Tests | What they establish |
| --- | --- | --- |
| Contracts (`packages/contracts`) | 20 | Units, geometry rules, provenance and adoption rules, run-state transitions, discipline matching, generated content refused in captured scenes |
| Scene (`packages/scene`) | 27 | Transform round trips and composition, Umeyama similarity, scale from references, georeference with projected CRSs, polygon validity, obstacle exclusion, connectivity, PLY reading and Gaussian detection, measurement evaluation |
| Metrics (`packages/metrics`) | 17 | Registry ids unique and documented; evacuation metrics including unattained T100; operations and collision metrics; time-series sampling; comparison rules for zero baselines and unavailable values |
| Engines (`packages/engines`) | 36 | Real reference workflows for JuPedSim, SUMO, SWMM, EPANET, SimPy and OpenFOAM; OpenStudio model translation; file-exchange packaging and import for FDS, Ventsim, FLAC3D, 3DEC, HEC-RAS, Virtual CRASH and PC-Crash; safe archive extraction; every catalogue engine has an adapter and a complete capability record |
| Core (`packages/core`) | 8 | Migrations equal the models and downgrade and upgrade cleanly; S3 storage against an S3 API emulator; restore filtering |
| API (`services/api`) | 42 | Authentication, isolation and roles; uploads and signed URLs; revisions; run lifecycle with real engines; validation failures; honest refusals; file exchange; quotas; worker-reported availability; reconstruction; comparisons, reviews and reports; CrashLens; optimisation; the assistant; demonstrations; backup and restore; the admin command |
| Simulation worker | 3 | Dispatch through the broker, cancellation that kills the engine process, recovery of a lost heartbeat until the retry budget is exhausted |
| Results worker | 5 | Chart scales and labels, table twins, step-hold sampling, and that the packaged logo is byte-identical to the supplied file |
| Browser (`apps/web/e2e`) | 17 | The workflow end to end in Chromium, access boundaries, and accessibility basics in both colour schemes |

## Engines

Every engine has at least one real reference workflow or, for licensed and operator-run engines, an
import test against the documented exchange format. Normalised outputs are compared with each engine's
native outputs, and the tolerances and their reasons are recorded in the
[engine verification record](engine-verification.md) (17 cross-checks across six engines, all passing).

| Engine | Version verified | Platform | Evidence | Status |
| --- | --- | --- | --- | --- |
| JuPedSim | 1.4.2 | Ubuntu 24.04, Python 3.13 and 3.12 (worker image) | 6 tests: accounting identity, closures making people unreachable and T100 unattained, short horizon leaving people remaining, matched populations, invalid geometry and closures refused; trajectories checked against prepared geometry | Verified |
| SUMO | 1.28.0 | same | 5 tests: demand accounting, rerouting around a closure, unfinished trips reported instead of a better travel time, seed reproducibility, signalised alternative | Verified |
| SWMM | 5.2.4 (swmm-toolkit 0.17.0) | same | 6 tests: volumes against the native report, blockage and storage effects, disconnected nodes refused, partial-pipe geometry, report parser, identical rainfall across scenarios; continuity errors reported as metrics | Verified |
| EPANET | 2.2 through WNTR 1.5.0 | same | 2 tests: mass balance and demand, exact added fire-flow volume | Verified |
| SimPy | 4.1.2 | same | 2 tests: conservation and completions against the event log, common random numbers across alternatives | Verified |
| OpenFOAM | v1912 | Ubuntu 24.04 package | 3 tests: template run with geometry cross-checks, overlapping obstacles refused, mesh cell count | Verified |
| EnergyPlus with OpenStudio | OpenStudio 3.11.0 | Ubuntu 24.04 | Model translation and exchange package; SQL result parser against the documented schema; managed mode reports EnergyPlus missing | OpenStudio preparation verified; EnergyPlus 25.2.0 execution awaiting environment |
| FDS | 6.10.1 (format) | n/a | Input generation and device CSV import | Awaiting environment (FDS not installed) |
| HEC-RAS | 6.6 (format) | n/a | Exchange metadata required (terrain, mesh, datum); raster import | Awaiting environment (operator-run) |
| Isaac Sim | 5.x | n/a | Reports *worker unavailable* without an RTX GPU; reference evaluation of imported states | Awaiting environment (GPU) |
| Virtual CRASH | operator's licensed version | n/a | Import computes residuals against evidence targets | Awaiting environment (licence) |
| Ventsim, FLAC3D, 3DEC | operator's licensed version | n/a | Licence gating; import with provenance; FLAC3D flags non-specialist properties | Not verified against an installation |
| PC-Crash | n/a | n/a | Import is refused until its export format is documented | Integration incomplete |

The current state of each installation on this host is in the [engine matrix](engine-matrix.md).

## Specification checks (section 30)

### 30.1 Geometry and coordinates

Tested: transform round trips, composition, inversion and decomposition (shear refused); quaternion
conventions; unit conversions, including dimension checks and temperature differences; scale from a
known reference and similarity fitting (collinear control points refused), with held-out check points
in the reconstruction test; polygon validity (unclosed rings, non-finite values, self-intersection);
obstacle exclusion from walkable areas; closures splitting walkable areas into components; graph
reachability used for drainage connectivity (disconnected SWMM nodes refused); local and geographic
round trips below a millimetre, with geographic CRSs refused for metric work and drainage models
requiring a vertical reference; measurement errors against tolerances. Terrain-to-model alignment uses
the same frame graph, but no test aligns a georeferenced terrain raster with a model yet (see the
[implementation status](implementation-status.md)).

### 30.3 Modules

| Module | Requirement | Evidence |
| --- | --- | --- |
| Mine Evacuation | Population accounting; unreachable people visible; unattained clearance null; trajectories agree with geometry | JuPedSim tests and the native cross-check "trajectory samples outside prepared walkable geometry" |
| CrashLens | Original hashes unchanged; derivatives keep source links; observations and hypotheses distinct; external analysis keeps provenance; case access enforced | `test_crashlens.py` (four tests, including database triggers that refuse changes to originals) and the Virtual CRASH import test |
| Construction | Resource conservation; queues and completions agree with events; scenario geometry reflected in the model | SimPy tests; `test_derived_model_reflects_prepared_geometry_and_leaves_out_proposals` |
| Urban Planning | Demand accounted for; unfinished journeys reported; comparisons use compatible intervals and demand | SUMO tests; comparison compatibility tests including the travel-time guard |
| Urban Drainage | Units; connections and elevations checked; continuity visible; flood volumes agree with source outputs | SWMM tests and the volume cross-checks against the native report |

### 30.4 AI

The assistant is tested with a scripted provider (deterministic model turns) against the real API and
tools, and the request sent to the Anthropic API is checked against its contract without network
access. Tests cover: use of authorised project data only, schema-valid drafts that cannot run until
adopted, missing inputs reported instead of invented, no invented metrics (results come from tool
output), no claim that an unexecuted run happened, tool authorisation that cannot be bypassed,
organisation policy for case records, observations kept distinct from interpretations, provider errors
and refusals shown rather than hidden, and the assistant reporting itself unavailable without
credentials. A live model was not exercised (no API key on the verification host).

## Access control

| Rule | Evidence |
| --- | --- |
| Projects in another organisation are invisible (404) | `test_project_isolation_and_roles`; browser test `access.spec.ts` |
| Project roles limit actions (viewer cannot edit, compare, adopt or run) | `test_project_isolation_and_roles`, `test_scoped_reviews_and_permissions`, `test_tools_enforce_the_users_permissions`, `access.spec.ts` |
| Case records need explicit assignment, even for organisation administrators | `test_case_access_requires_explicit_assignment` |
| Case-derived models, scenarios, runs, counts and activity are hidden from people outside the case | `test_crashlens.py`; `access.spec.ts` (an unassigned project viewer sees no case runs) |
| Evidence media is not reachable through ordinary asset routes | `test_evidence_originals_are_preserved` |
| Assistant tools act with the user's own permissions and the organisation's AI policy | `test_assistant.py` |
| Removing a member removes access | `access.spec.ts` |
| Downloads need a valid signature; a tampered one is refused | `test_upload_verification_hash_and_signed_url` |

## Backup and restoration

- **Test:** `test_backup_restores_into_an_empty_deployment_and_verifies` backs up a deployment with real
  runs, reports and documents, restores into an empty database and storage, verifies counts and every
  hash, refuses to restore over existing data, and detects a changed object and a damaged archive.
- **Development deployment:** all five demonstrations (43 tables, 1,100 rows, 332 objects, 48.7 MB):
  backup 2.3 s, restore and verification 2.4 s, 200 recorded hashes re-checked, no problems.
- **Containers:** see below.

## Container deployment

On 2026-10-23 the images were built from this repository and the compose stack was run:

1. All services healthy; the web container serves the application with security headers on every
   route and forwards `/api`.
2. The first administrator was created with the admin command.
3. The five demonstrations were seeded inside the worker container in 61 s, running JuPedSim, SUMO,
   SWMM, SimPy and COLMAP on the image's Python 3.12, and generating PDF reports.
4. Runs submitted through nginx for SWMM, JuPedSim and SUMO were queued, executed by the simulation
   worker container and completed through every state.
5. The 17 browser tests passed against the containerised deployment.
6. A backup made inside the worker container (44 tables, 961 rows, 378 objects, 57.9 MB) was restored
   into a second, empty deployment on the PostGIS image (which also carries the topology and tiger
   extensions); verification re-checked 246 recorded hashes with no problems, and the 17 browser tests
   passed against the restored deployment.

Defects found and fixed during this verification: files unreadable by the unprivileged user because of
build-context file modes; nginx dropping security headers inside locations; the API refusing runs its
own host could not execute (now resolved from worker reports); no PostgreSQL client for backups; and
restoration recreating schemas the PostGIS image already provides.

## Browser tests

`apps/web/e2e` (Playwright, Chromium with software WebGL): sign-in with a wrong and a right password;
the five demonstrations and their synthetic labels; the scene workspace drawing prepared geometry,
selecting an object to see its provenance, and playing back an engine trajectory; a run's provenance,
native cross-checks, metrics and chart tables; the controlled comparison with the recorded T100; the
engine library's honest availability; CrashLens evidence verification, observations and
interpretations, hypotheses without probabilities, and the reconstructed point cloud with its checks;
access boundaries for organisation, project and case; one main landmark, one level-one heading and a
working skip link on every page; status text alongside colour; labelled form fields. Any browser
console error fails the test that caused it. The suite passed against the development server, the
containerised deployment and the restored deployment.

The browser tests also found a server defect: the API committed each request's transaction after the
response had been sent (the default scope of a FastAPI dependency with `yield`), so a client could
receive 201 and act before the write existed; a member created and signed in at once was refused. The
transaction now commits before the response, a test guards the setting, and the suite has since passed
repeatedly in full.

## Performance

Measured workloads with the host recorded are in [benchmarks.md](benchmarks.md). The viewer benchmark
found that the playback controls enabled only after an unrelated re-render (11.6 s); after the fix a
run is playable in 1.7 s.

## Release acceptance (section 30.6)

| Criterion | State |
| --- | --- |
| The five demonstrations work end to end | Met: seeded from real runs in development and in containers; browser tests pass |
| Every engine has a verified supported integration pathway | Partly met: six managed engines and OpenStudio verified; the others have implemented pathways verified against their exchange formats, awaiting an installation, GPU or licence (table above) |
| Results are traceable | Met: revisions, hashes, versions, seeds and cross-checks on every run and in every report |
| Access control works | Met: tests above |
| Reports reflect actual runs | Met: reports are generated from the run records' manifest; tests check PDF, CSV and JSON agree |
| Remaining scientific limitations are documented | Met: per engine in the [engine matrix](engine-matrix.md) and in the [implementation status](implementation-status.md) |
| No unfinished capability is represented as operational | Met: availability is reported from real checks; unfinished items are listed in the implementation status |
