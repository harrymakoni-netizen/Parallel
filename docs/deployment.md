# Deployment

Parallel deploys as a browser application, an API, a PostgreSQL 16 database with PostGIS, Redis, object
storage and queue workers. The browser application, the API and each kind of worker can run on
separate hosts (spec 28.3). This guide covers local development, a single-host container deployment,
hosted and GPU deployments, licensed engines, and operations.

## Local development

See the quick start in the [README](../README.md). In short: PostgreSQL 16 with PostGIS and Redis
running locally, `uv sync`, `scripts/setup_runtimes.sh`, `cp .env.example .env`,
`uv run alembic upgrade head`, the admin command, then the API, the four Celery processes and
`npm run dev` in `apps/web`. The Vite development server proxies `/api` to `PARALLEL_API_URL`
(default `http://localhost:8000`).

## Single host with Docker Compose

`compose.yaml` runs the whole platform: `db` (PostGIS 16), `redis`, `migrate` (runs once), `api`,
`results`, `scheduler`, `sim`, `recon` and `web` (nginx serving the application and forwarding `/api`).

```bash
cp .env.example .env
# set PARALLEL_SECRET_KEY (python -c "import secrets; print(secrets.token_urlsafe(48))"),
# POSTGRES_PASSWORD, and PARALLEL_PUBLIC_BASE_URL / PARALLEL_WEB_ORIGIN to the address users will use
docker compose up -d --build
docker compose run --rm api python -m parallel_api.admin create-admin \
    --email you@example.org --name "Your name" --organisation "Your organisation"
```

The admin command prompts for the password, or reads `PARALLEL_BOOTSTRAP_PASSWORD` for unattended
setup; it never takes a password on the command line. Further people are added by an administrator in
Organisation Settings. Optional demonstration data: `docker compose run --rm sim python -m parallel_api.demo seed`.

Only the web port (`PARALLEL_HTTP_PORT`, default 8080) is published. Put a TLS-terminating proxy or load
balancer in front of it for anything beyond a workstation.

**What was verified.** On 2026-10-23 this stack was built from this repository and run on one host:
all services healthy, the administrator bootstrapped, the five demonstrations seeded inside the worker
container (every managed engine, COLMAP and the PDF reports), runs submitted through nginx and executed
by the simulation worker container, the 17 browser end-to-end tests passed against it, and a backup
restored into a second, empty deployment that passed the same 17 tests. Details are in
[validation.md](validation.md#container-deployment).

### Images

| Image | Dockerfile | Base | Contents |
| --- | --- | --- | --- |
| `parallel-app` | `infra/docker/app.Dockerfile` | `python:3.13-slim` | API, results worker, scheduler, migrations, admin commands |
| `parallel-worker` | `infra/docker/worker.Dockerfile` | `ubuntu:24.04` | Simulation and reconstruction workers: JuPedSim, SUMO, SWMM, EPANET, SimPy, OpenStudio runtimes, OpenFOAM v1912, COLMAP, FFmpeg, PostgreSQL 16 client (backups), demonstration seeding |
| `parallel-web` | `infra/docker/web.Dockerfile` | `node:22` build, `nginx:1.27-alpine` | The built application and the `/api` proxy with security headers |

The worker image uses Ubuntu 24.04, the platform the engines were verified on, with its Python 3.12;
each Python engine has its own isolated runtime under `/app/.runtimes`. Images run as an unprivileged
user (uid 10001). Approximate sizes: app 1.6 GB, worker 4.9 GB, web 90 MB.

Build arguments:

| Argument | Default | Use |
| --- | --- | --- |
| `PYTHON_IMAGE`, `UBUNTU_IMAGE`, `NODE_IMAGE`, `NGINX_IMAGE` | Docker Hub official images | Point at a registry mirror, e.g. `mirror.gcr.io/library/ubuntu:24.04` |
| `UV_VERSION` | 0.11.32 | uv used to install the locked dependencies |

Behind a TLS-inspecting proxy, give the build the proxy settings and its certificate bundle as a
build secret; the certificate is mounted only while dependency steps run and is not stored in the image:

```bash
docker build --build-arg HTTPS_PROXY=$HTTPS_PROXY --secret id=ca,src=/path/to/ca-bundle.crt \
    -f infra/docker/worker.Dockerfile -t parallel-worker .
```

## Hosted and distributed deployments

| Part | Notes |
| --- | --- |
| Web application | Static files (`npm run build` → `apps/web/dist`) on any static host or CDN, with `/api` routed to the API. Keep `try_files` style fallback to `index.html` for client-side routes. |
| API | `parallel-app`, horizontally scalable behind a load balancer; stateless apart from the database, Redis and storage. Set `PARALLEL_ENVIRONMENT=production`. |
| Database | Managed PostgreSQL 16 with PostGIS available. Run `alembic upgrade head` (the `migrate` service) on every release. |
| Redis | Queue broker and progress events. Persistence is not required: tasks are acknowledged late and the recovery sweep re-queues work whose worker stopped. |
| Storage | `PARALLEL_STORAGE_BACKEND=s3` with any S3-compatible store (bucket, region, endpoint, keys in `.env`). Signed URLs are presigned by the store. The S3 backend is tested against an S3 API emulator; verify it against your provider during commissioning. Local storage needs a volume shared by the API and every worker. |
| CPU workers | `parallel-worker` with `-Q cpu,maintenance` (simulation) and `-Q reconstruction` (capture processing). Scale by running more containers or raising `-c`. |
| Results worker | `parallel-app` with `-Q results`. |
| Scheduler | Exactly one `celery beat` (recovery sweep every minute, engine availability every five minutes). |

### HTTPS on a single host

`compose.https.yaml` adds Caddy in front of the stack, which obtains and renews a certificate for
`PARALLEL_DOMAIN`. Point the name's DNS record at the host, open ports 80 and 443, set
`PARALLEL_DOMAIN`, `PARALLEL_PUBLIC_BASE_URL` and `PARALLEL_WEB_ORIGIN` (both `https://` and the same
name) in `.env`, and start with:

```bash
docker compose -f compose.yaml -f compose.https.yaml up -d --build
```

The web container then publishes no port of its own. A full Google Cloud walk-through is in
[google-cloud.md](google-cloud.md).

### Browser application on Vercel

`apps/web/vercel.json` deploys the browser application to Vercel (root directory `apps/web`, Vite build,
client-side routes served by `index.html`, the same security headers as the nginx image). Vercel hosts
only the browser application: the API, workers, database and engines run elsewhere (above).

Until an API is connected, every `/api` request is answered by `apps/web/api/unavailable.js` with a
503 in the API's error shape, so the sign-in page explains that the backend is not connected rather
than failing obscurely. To connect a hosted API, replace the first rewrite in `vercel.json` with

```json
{ "source": "/api/(.*)", "destination": "https://your-api.example.org/api/$1" }
```

and set the API's `PARALLEL_WEB_ORIGIN` and `PARALLEL_PUBLIC_BASE_URL` to the Vercel address. Large
capture uploads and long progress streams pass through Vercel's proxy, which has its own size and
duration limits; for heavy use, serve the application from the nginx image beside the API instead.

### Engine availability across hosts

Simulation workers report which managed engines they can run when they start and every five minutes;
the API trusts reports younger than fifteen minutes when its own host has no engine installed. A worker
that stops is forgotten once its reports expire, so the Engine Library and run submission always
reflect workers that are actually present. Connected and file-exchange modes depend on the
organisation's registered installations instead (Organisation Settings → Engine installations).

### GPU worker (Isaac Sim, Gaussian splat training)

Run a simulation worker on an RTX host with `-Q gpu`, with Isaac Sim installed and
`PARALLEL_ISAACSIM_PYTHON` pointing to its `python.sh`; for Gaussian training also install nerfstudio
(`ns-train`) on the reconstruction worker's host. Neither was available on the verification host, so
both are *awaiting environment verification* ([implementation status](implementation-status.md)).
Without a GPU worker, Isaac Sim reports *Worker unavailable* and reconstructions publish an untrained
Gaussian initialisation, labelled as such.
To use a free or rented GPU without connecting it to the deployment, train from a training package and
import the result: see [external training](external-training.md).

### Licensed and operator-run engines

| Engine | How to enable |
| --- | --- |
| FDS | Install FDS 6.10.1 on the worker and set `PARALLEL_FDS_BIN`, or use file exchange |
| EnergyPlus | Install EnergyPlus 25.2.0 and set `PARALLEL_ENERGYPLUS_BIN` (OpenStudio is in the worker image), or use file exchange |
| HEC-RAS | File exchange: the operator runs HEC-RAS 6.6 and uploads the result package |
| Ventsim, FLAC3D, 3DEC, Virtual CRASH, PC-Crash | Register the organisation's licensed installation (edition, version, licence holder, operator contact, licence confirmation) in Organisation Settings; runs then prepare an input package, wait for the operator and import the result package with its provenance |

Parallel never runs a licensed engine it cannot see, and never substitutes another engine; until an
installation is registered and checked, the engine shows its real state and the next step.

## Configuration

Every setting is a `PARALLEL_*` environment variable, documented in [`.env.example`](../.env.example).
The ones that matter most in production:

| Setting | Why |
| --- | --- |
| `PARALLEL_ENVIRONMENT=production` | Refuses to start with a weak or default signing key |
| `PARALLEL_SECRET_KEY` | Signs sessions and download URLs; at least 32 random characters |
| `PARALLEL_PUBLIC_BASE_URL`, `PARALLEL_WEB_ORIGIN` | Signed URL host and the allowed browser origin |
| `PARALLEL_MAX_UPLOAD_BYTES`, `PARALLEL_MAX_ARCHIVE_TOTAL_BYTES` | Upload and result-package limits |
| `PARALLEL_ORG_CONCURRENT_RUN_LIMIT`, `PARALLEL_ORG_MONTHLY_RUN_QUOTA` | Per-organisation run limits (refused with 429) |
| `PARALLEL_HEARTBEAT_TIMEOUT_S`, `PARALLEL_MAX_RUN_ATTEMPTS` | Recovery of runs whose worker stopped |
| `PARALLEL_ENGINE_THREADS` | Threads each engine process may use |
| `PARALLEL_ASSISTANT_PROVIDER`, `PARALLEL_ANTHROPIC_API_KEY` | The optional assistant; without a key it reports itself unavailable |

**Sign-in.** Local accounts (scrypt-hashed passwords, signed session tokens) are the supported mode.
The API can also verify OIDC tokens against a provider's keys (`PARALLEL_AUTH_MODE=oidc` with issuer,
audience and JWKS URL), but the browser sign-in flow and account linking for OIDC are not implemented
yet; see the [implementation status](implementation-status.md).

## Backups

`python -m parallel_core.backup` backs up the database and every stored object together and proves a
restore. Run it from the worker image, which has the PostgreSQL 16 client and the storage volume:

```bash
mkdir -p backups && chmod 777 backups   # writable by the container user
docker compose run --rm --no-deps -v "$PWD/backups:/backups" sim \
    python -m parallel_core.backup create --out /backups/$(date +%F)
docker compose run --rm --no-deps -v "$PWD/backups:/backups" sim \
    python -m parallel_core.backup verify --from /backups/2026-10-23
```

A backup directory holds `db.dump` (PostgreSQL custom format), `objects.tar` (every object, keys as
paths) and `manifest.json` (schema revision, row counts, the size and SHA-256 of every object and of
both archives, and any database reference to a missing object).

To restore, start an **empty** deployment (fresh volumes, database with PostGIS available), then run
`restore` before anything else writes to it:

```bash
docker compose up -d db redis
docker compose run --rm --no-deps -v "$PWD/backups:/backups" sim \
    python -m parallel_core.backup restore --from /backups/2026-10-23
docker compose up -d
```

`restore` checks both archives against the manifest first, refuses a database with tables or storage
with objects, leaves extensions and their schemas to the target server, and finishes by verifying the
schema revision, every table's row count, every object's hash, and that every original and run artifact
still matches the SHA-256 recorded when it was received. Any difference fails loudly.

With S3 storage, the same commands copy objects through the API's storage interface; for large stores
use the provider's versioning or replication as well, and keep database dumps and object snapshots
from the same time.

## Upgrades

1. Back up (above).
2. Build or pull the new images.
3. `docker compose up -d`: the `migrate` service applies new migrations before the API and workers
   start. Migrations are tested to match the models and to downgrade and upgrade cleanly.
4. Check `GET /api/v1/health` and the Engine Library.

Runs in progress when a worker restarts are re-queued by the recovery sweep while attempts remain.

## Operations

- **Health:** `GET /api/v1/health` (API and database). Container health checks use it.
- **Logs:** each service logs to standard output; run, reconstruction and engine logs are also stored
  as artifacts with their runs and jobs.
- **Run records:** queue, start and finish times, attempts, worker, engine and adapter versions,
  per-state durations and output sizes are on every run; reconstruction jobs record per-stage timings.
- **Demonstrations:** `python -m parallel_api.demo status|reset`.

## Security checklist

- TLS in front of the web container; only that port exposed.
- Strong `PARALLEL_SECRET_KEY` and `POSTGRES_PASSWORD`; `.env` readable only by the operator.
- `PARALLEL_WEB_ORIGIN` and `PARALLEL_PUBLIC_BASE_URL` set to the real address.
- Organisation AI policy reviewed (Organisation Settings): external AI access, and case media to AI
  (off by default).
- Backups scheduled and a restore rehearsed.
- Licensed engines registered only with confirmed licences.
