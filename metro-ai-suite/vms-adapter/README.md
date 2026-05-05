




# VMS Adapter Plugin

I/O bridge between NVR/VMS systems (Frigate, Nx Witness) and a downstream Core App
(Live Video Captioning). Combines a FastAPI backend, NVR/Core-App shims, and a React
operator UI into a single deployable unit.

```
NVR (Frigate / Nx Witness) ──▶ [Folder Watcher] ──▶ [Plugin Backend] ──▶ [Live Captioning Core App]
                              ◀── [Command Shim] ◀── [Results Handler] ◀──
                                                      ▲
                                                      │
                                                  React UI (nginx)
```

## Folder Layout

```
vms-adapter-plugin/
├── plugin/                     # Backend (Python package: plugin)
│   ├── base/                   #  Abstract interface (IVmsShim, ICoreAppShim)
│   ├── core/                   #  Business logic
│   │   ├── api/                #   FastAPI routes + middleware + deps
│   │   ├── db/                 #   SQLAlchemy session + repository
│   │   ├── models/             #   ORM + domain models
│   │   ├── pipeline/           #   Orchestrator + results handler
│   │   ├── config.py           #   Pydantic settings (YAML + env)
│   │   ├── factory.py          #   Shim factory
│   │   └── main.py             #   FastAPI entry point
│   └── common/                 #  Shared utilities (placeholder)
├── vms_shim/                   # Concrete shims (Python package: vms_shim)
│   ├── nx_witness/             #  NxWitnessVmsShim
│   ├── frigate/                #  FrigateVmsShim
│   └── core_app/               #  LiveCaptioningCoreAppShim
│   └── core_app/               #  LiveCaptioningCoreAppShim
├── ui/                         # React/Vite frontend, served by nginx
├── alembic/                    # DB migrations
├── config/                     # Default + dev YAML configs
├── tests/                      # pytest unit + integration tests
├── Dockerfile                  # Backend image
├── docker-compose.yml          # Combined service: backend + ui + postgres
├── pyproject.toml              # Backend deps + package config
└── .env.example                # Sample environment
```

> Python identifiers cannot contain hyphens, so backend folders use underscores
> (`vms_shim`, `nx_witness`, `core_app`).

## Quick Start (Docker)

```bash
cp .env.example .env
# Edit .env: NX_BASE_URL, NX_USERNAME, NX_PASSWORD, FRIGATE_HOST,
# LVC_BASE_URL, MEDIAMTX_URL, recording paths, etc.

docker compose up -d --build
```

| Service  | URL                          |
| -------- | ---------------------------- |
| UI       | http://localhost:3000        |
| Backend  | http://localhost:8085/v1     |
| API docs | http://localhost:8085/docs   |
| Postgres | localhost:5433               |

The UI's nginx reverse-proxies `/v1/*` and the SSE `/v1/live-captioning/stream`
endpoint to the `backend` service in the same Docker network.

## Local Development

### Backend

```bash
uv pip install -e ".[dev]"           # or: pip install -e ".[dev]"
export VMS_PLUGIN_CONFIG_PATH=$PWD/config/config.dev.yaml
alembic upgrade head
uvicorn plugin.core.main:app --reload --port 8082
pytest tests/ -v
```

### UI

```bash
cd ui
npm install
npm run dev                          # http://localhost:5173 with /v1 proxy
npm run build
```

## API Endpoints

| Method | Path                                       | Description                              |
| ------ | ------------------------------------------ | ---------------------------------------- |
| GET    | `/v1/health`                               | Liveness probe                           |
| GET    | `/v1/ready`                                | Readiness (DB + Core App + VMS checks)   |
| GET    | `/v1/cameras`                              | List persisted cameras                   |
| GET    | `/v1/cameras/{id}`                         | Get a single camera                      |
| GET    | `/v1/cameras/discover`                     | Discover cameras across all NVRs         |
| POST   | `/v1/cameras/enable`                       | Enable / disable cameras                 |
| GET    | `/v1/events/timeline`                      | Paginated metadata-event timeline        |
| POST   | `/v1/analysis/results`                     | Async callback from Core App             |
| GET    | `/v1/config/status`                        | Loaded config + uptime                   |
| GET    | `/v1/live-captioning/pipelines`            | Available LVC pipelines (CPU/GPU)        |
| GET    | `/v1/live-captioning/models`               | Available VLM models                     |
| POST   | `/v1/live-captioning/runs`                 | Start a captioning run                   |
| GET    | `/v1/live-captioning/runs`                 | List active runs                         |
| GET    | `/v1/live-captioning/runs/{run_id}`        | Get run status                           |
| DELETE | `/v1/live-captioning/runs/{run_id}`        | Stop a run                               |
| GET    | `/v1/live-captioning/stream`               | SSE stream of captions                   |

## Configuration

`config/config.yaml` is the default template; values use `${ENV_VAR}` /
`${ENV_VAR:-default}` placeholders resolved at startup from the environment
or `.env`. Switch profiles by setting `VMS_PLUGIN_CONFIG_PATH`.

Only the `live_captioning` Core App is supported.

## Tech Stack

- **Backend:** Python 3.10+, FastAPI, Uvicorn, SQLAlchemy 2 (async), PostgreSQL 15,
  Pydantic v2, Alembic, Watchdog, httpx, structlog.
- **Frontend:** React 19, Vite 8, Tailwind 4, shadcn/base-ui, sonner.
- **Infra:** Docker Compose (backend + nginx + postgres).
