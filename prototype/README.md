# Parallel

Platform MVP: **Capture → Reconstruct → Prepare → Simulate → Compare → Improve**

Sellable UI: **React + TypeScript + Vite** workbench talking to **FastAPI**.

| App | Status |
|---|---|
| Mine evacuation | **MVP** (plan compare + metrics) |
| CrashLens | Planned module |
| Construction | Planned module |
| Urban planning | Planned module |
| Flood management | Planned module |

## Hardware note

- This workstation: GTX 1650 / **4 GB VRAM** — fine for the web app + CPU sims.
- 3D Gaussian Splatting training needs **≥8 GB**; run that in cloud later.
- Customers use a **browser**; heavy jobs belong on hosted workers ([docs/COMMERCIAL.md](docs/COMMERCIAL.md)).

## Setup

```powershell
cd c:\Users\user\Documents\Parallel
.\.venv\Scripts\Activate.ps1
uv sync
cd frontend
npm install
```

## Run the product UI

```powershell
.\scripts\run_web.ps1
```

- App: http://127.0.0.1:5173  
- API docs: http://127.0.0.1:8000/docs  

Or separately:

```powershell
.\scripts\run_api.ps1
cd frontend
npm run dev
```

## Smoke test (engine only)

```powershell
.\scripts\smoke_test.ps1
```

## Project layout

```
frontend/              # React workbench (customer-facing UX)
src/parallel/          # Models, adapters, FastAPI
apps/streamlit_app.py  # Internal lab only — not the product UI
docs/                  # Environment, commercial, pilot
scripts/               # run_web / run_api / smoke_test
```

## Docs

- [Environment](docs/ENVIRONMENT.md)
- [Commercial packaging](docs/COMMERCIAL.md)
- [Mine Evac pilot](docs/PILOT_MINE_EVAC.md)

## Next steps

1. JuPedSim adapter for R1 pedestrian physics.
2. Preparation workspace (edit polygons, scale calibration).
3. CrashLens evidence module on the same contracts.
4. Cloud workers for reconstruction / larger sims.
