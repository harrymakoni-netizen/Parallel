# Working on Parallel

Read `README.md` for what Parallel is, `docs/architecture.md` for how it fits together, and
`docs/PARALLEL_DEVELOPMENT_SPEC.md` for the requirements. `docs/implementation-status.md` must stay
accurate: update it in the same change as anything it describes.

## Commands

```bash
uv sync                                         # Python workspace (packages/*, services/*, workers/*)
scripts/setup_runtimes.sh [engine...]           # isolated engine runtimes under .runtimes/
uv run alembic upgrade head                     # migrate the database in PARALLEL_DATABASE_URL
uv run pytest                                   # all Python tests (needs PostgreSQL+PostGIS and Redis)
uv run pytest services/api/tests/test_crashlens.py -k evidence   # one area
uv run ruff check .                             # lint (line length 110)
uv run python scripts/reference_docs.py         # regenerate docs/metrics.md, api.md, openapi.json, engine-matrix.md
uv run python scripts/engine_verification.py    # run reference workflows; regenerate docs/engine-verification.md
uv run python scripts/benchmark.py              # benchmark workloads (see its docstring); --summary writes docs/benchmarks.md
uv run python -m parallel_api.demo seed|status|reset
uv run python -m parallel_api.admin create-admin --email ... --organisation ...
uv run python -m parallel_core.backup create|restore|verify ...
cd apps/web && npm run typecheck && npm run build
cd apps/web && E2E_PASSWORD=... npm run e2e     # browser tests against a running stack with demos seeded
```

Tests create disposable databases from the `parallel_template` database (PostGIS only) and use Redis
database 15. Engine tests run the real engines and fail when a runtime is missing; set
`PARALLEL_ALLOW_MISSING_ENGINES=1` only on a machine that cannot host them, and never in CI.

## Non-negotiable rules

These come from the specification and are enforced by tests. Do not weaken them to make something pass.

- **No invented results.** Metrics come only from engine outputs through an adapter's normaliser, and
  every metric id must exist in `packages/metrics` (an unknown id fails the run). Unattained or
  unavailable values are reported as such, never estimated. No timers or animations stand in for work.
- **Honest availability.** An engine that is not installed, licensed or connected is shown with its
  real state and a next step. Never fall back to a different engine or a stub silently.
- **Provenance on every input.** Values in models and scenes carry a `Provenance`
  (`packages/contracts/.../provenance.py`). AI-suggested values stay `ai_suggested` until a person
  adopts them; runs refuse unadopted AI content.
- **Reproducibility.** Published revisions are immutable; edits create new revisions. Runs record
  revisions, hashes of every input and output, seeds, engine and adapter versions.
- **Visual is not physical.** Captured appearance is never treated as walkable or solid geometry;
  prepared objects are separate, simplified and editable. A reconstruction never implies scale,
  measurement or calibration quality states.
- **CrashLens neutrality.** Observations and interpretations are separate; interpretations cite the
  observations they use. Parallel never computes a probability that a hypothesis is true and never
  determines fault. Evidence originals are immutable (database triggers) and hashed at receipt.
- **Access is decided on the server** for every request: organisation and project roles, and case
  assignment for anything case-scoped (`require_scoped`). Lists and counts must not leak case records.
- **Do not copy code from the reference Gaussian Splatting repository**; it has no licence. The
  pipeline in `workers/reconstruction` is an independent implementation and credits it.
- **The logo** (`brand/parallel-logo.webp` and its copies) is used unaltered and never redrawn.

## Conventions

- Python 3.13 (3.12 supported), Pydantic v2 contracts, SQLAlchemy 2 typed models, Alembic migrations
  (`packages/core/migrations`; `test_migrations` checks models and migrations agree).
- API errors use `ApiError(status, code, message)` and the `{"error": {...}}` shape; consequential
  changes write an audit event (`parallel_core.audit.record`).
- Long work runs in Celery tasks (`parallel_core.jobs`): queues `cpu`, `gpu`, `reconstruction`,
  `results`, `maintenance`. The API dispatches by task name and never imports worker code.
- Engine adapters live in `packages/engines/.../adapters`; each declares a capability record, an
  installation check, input materialisation, execution through a runner, normalisation and native
  cross-checks. Engines run in isolated runtimes with controlled templates; never execute uploaded code.
- Web: React 19 + TypeScript, TanStack Query, design tokens in `src/styles/tokens.css` (dark default,
  light by preference or toggle). Status is always icon plus text; every chart has a table view;
  respect reduced motion. Scene frame: right-handed, Z up, metres.
- Comments explain why, not what. British English in user-facing text.
