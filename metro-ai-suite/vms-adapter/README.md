# VMS Adapter Plugin

An I/O bridge between VMS systems (**Frigate**, **Nx Witness**) and AI Core Apps
(**Live Video Captioning**). Combines a FastAPI backend, pluggable VMS shims, a
generic Core App API, and a React operator dashboard into a single Docker Compose
deployment.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        VMS Adapter Plugin                           │
│                                                                     │
│  ┌──────────┐    ┌─────────────────┐    ┌──────────────────────┐   │
│  │ Frigate  │    │  FastAPI Backend │    │  Live Video          │   │
│  │ (VMS)   ├───►│  (plugin/)       ├───►│  Captioning (LVC)    │   │
│  └──────────┘    │                 │    │  Core App            │   │
│  ┌──────────┐    │  - Camera sync  │    └──────────┬───────────┘   │
│  │Nx Witness│    │  - Generic runs │               │               │
│  │ (VMS)   ├───►│  - Result proxy │    ┌──────────▼───────────┐   │
│  └──────────┘    └────────┬────────┘    │  MediaMTX (WebRTC)   │   │
│                           │             │  MQTT Broker         │   │
│                  ┌────────▼────────┐    └──────────────────────┘   │
│                  │   React UI      │                                │
│                  │   (nginx)       │                                │
│                  └─────────────────┘                                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

Before starting the VMS Adapter Plugin, the following services must already be running on the host machine:

| Service | Where | Default Port |
|---|---|---|
| **Live Video Captioning (LVC)** | `metro-ai-suite/live-video-analysis/live-video-captioning/` — follow its own README | `4173` |
| **MediaMTX** (WebRTC signalling) | Included inside the LVC docker-compose stack | `8889` |

> **Why LVC first?** The plugin fetches the LVC OpenAPI schema at startup to build the dynamic
> run form. If LVC is unreachable the plugin will start but the analytics dashboard will show an
> error until LVC becomes available.

---

## Folder Layout

```
vms-adapter/
├── plugin/                         # Backend Python package
│   ├── base/
│   │   └── interfaces.py           #  IVmsShim + ICoreAppShim abstract interfaces
│   ├── common/
│   │   └── schema_builder.py       #  Dynamic Pydantic model builder from JSON Schema
│   └── core/
│       ├── api/
│       │   ├── routes/
│       │   │   ├── cameras.py      #   Camera discovery + enable/disable
│       │   │   ├── core_apps.py    #   Generic Core App API (discover, runs, stream, options)
│       │   │   ├── events.py       #   Event timeline
│       │   │   ├── analysis.py     #   Analysis result callback
│       │   │   ├── sessions.py     #   Session tracking
│       │   │   ├── vms.py          #   VMS register
│       │   │   ├── health.py       #   Health + readiness
│       │   │   └── config.py       #   Config status
│       │   └── deps.py             #   FastAPI dependency injection
│       ├── db/
│       │   └── repository.py       #   Async SQLAlchemy CRUD
│       ├── models/
│       │   ├── db.py               #   ORM models (Camera, Event, Session, …)
│       │   └── domain.py           #   Domain dataclasses
│       ├── pipeline/
│       │   └── orchestrator.py     #   Background camera sync + event processing
│       ├── config.py               #   Pydantic settings (YAML + env)
│       ├── factory.py              #   Shim factory
│       └── main.py                 #   FastAPI application entry point
│
├── vms_shim/                       # Concrete VMS shims
│   ├── frigate/
│   │   ├── shim.py                 #  FrigateVmsShim — discovers cameras via local config
│   │   └── config/                 #  Frigate config.yml (cameras, go2rtc, etc.)
│   └── nxwitness/
│       └── shim.py                 #  NxWitnessVmsShim — Nx Witness REST API v4
│
├── core_app_shim/                  # Concrete Core App shims
│   └── lvc/
│       ├── api_client.py           #  LvcApiClient — all HTTP calls to LVC backend
│       ├── schema.py               #  LvcSchemaManager — OpenAPI fetch, $ref resolution,
│       │                           #    UI annotations, Pydantic model building
│       └── shim.py                 #  LiveCaptioningCoreAppShim — composes api_client + schema
│
├── ui/                             # React 19 / Vite frontend served by nginx
│   ├── src/
│   │   ├── App.jsx                 #  Root component + state
│   │   ├── components/MainPage/
│   │   │   ├── CameraDiscoveryPanel.jsx
│   │   │   ├── AnalyticsEnginePanel.jsx   # Dynamic schema form + run lifecycle
│   │   │   ├── SchemaForm.jsx             # Generic JSON Schema → form renderer
│   │   │   ├── LiveStreamTab.jsx          # WebRTC player + caption overlay
│   │   │   └── AnalysisResultsPanel.jsx
│   │   ├── hooks/
│   │   │   └── useLvcStream.js     #  SSE caption stream hook
│   │   └── services/
│   │       └── api.js              #  Generic API client functions
│   └── nginx.conf                  #  Reverse proxy: /v1 → backend, /whep → MediaMTX
│
├── config/
│   └── config.yaml                 # Runtime config (cameras, VMS endpoints, LVC URL)
├── tests/                          # pytest unit + integration tests
├── Dockerfile                      # Backend image
├── docker-compose.yml              # backend + ui + postgres + frigate
├── pyproject.toml                  # Python deps + package config
└── .env.example                    # Environment variable reference
```

---

## Quick Start (Docker)

### Step 1 — Start LVC (prerequisite)

```bash
# From the LVC directory (separate terminal):
cd metro-ai-suite/live-video-analysis/live-video-captioning/
docker compose up -d
```

Verify LVC is reachable:

```bash
curl http://localhost:4173/health
```

---

### Step 2 — Create the `.env` file

```bash
cd metro-ai-suite/vms-adapter
cp .env.example .env
```

Open `.env` and update the variables for your environment. Key variables to set:

| Variable | Description |
|---|---|
| `LVC_BASE_URL` | URL of the running LVC backend (e.g. `http://<lvc-host>:4173`) |
| `MEDIAMTX_URL` | URL of the MediaMTX WebRTC server (e.g. `http://<lvc-host>:8889`) |
| `FRIGATE_HOST` | Hostname/IP of the Frigate instance reachable from the backend container |
| `NX_BASE_URL` / `NX_USERNAME` / `NX_PASSWORD` | Nx Witness credentials (only if using Nx) |
| `PG_PASSWORD` | Postgres password (change from default) |
| `BACKEND_PORT` / `UI_PORT` | Host ports for the API (`8085`) and dashboard (`3100`) |

> If LVC is running on the same host, `host.docker.internal` works on Linux/Mac. Otherwise replace it with the actual IP address.

---

### Step 3 — Configure Frigate cameras

Edit `vms_shim/frigate/config/config.yml` to add your camera RTSP streams.
The plugin reads this file directly — no Frigate API call is needed for camera discovery.
Refer to the [Frigate configuration docs](https://docs.frigate.video/configuration/) for the full schema.

---

### Step 4 — Build and start

```bash
docker compose up -d --build
```

Wait for all services to become healthy:

```bash
docker compose ps
```

Expected output — all services should show **healthy** or **running**:

```
NAME              STATUS
vms-backend       Up (healthy)
vms-ui            Up
postgres          Up (healthy)
frigate           Up
```

---

### Step 5 — Open the dashboard

| Service | URL |
|---|---|
| **Operator Dashboard** | http://localhost:3100 |
| **Backend API** | http://localhost:8085/v1 |
| **API Docs (Swagger)** | http://localhost:8085/docs |
| **Frigate UI** | http://localhost:5000 |
| **Postgres** | localhost:5433 |

Verify the backend is up:

```bash
curl http://localhost:8085/v1/health
```

Discover cameras from Frigate:

```bash
curl -X POST http://localhost:8085/v1/cameras/discover
```

---

### Stopping the stack

```bash
docker compose down          # stop without removing data
docker compose down -v       # stop and remove Postgres volume
```

---

## Architecture Overview

### VMS Shims (`vms_shim/`)

Each VMS vendor is represented by a class implementing `IVmsShim`:

| Shim | Source | Camera discovery |
|---|---|---|
| `FrigateVmsShim` | Frigate 0.15 | Reads local `config/config.yml` directly |
| `NxWitnessVmsShim` | Nx Witness REST v4 | Queries `/rest/v4/devices` |

The orchestrator runs a background loop that periodically syncs cameras from all
registered shims into the Postgres camera table.

### Generic Core App API

All AI pipeline integrations share a single generic route group. Adding a new
Core App requires only a new shim class — **zero route changes**.

```
ICoreAppShim  (plugin/base/interfaces.py)
└── LiveCaptioningCoreAppShim  (core_app_shim/lvc/shim.py)
    ├── LvcApiClient   — HTTP calls to LVC backend
    └── LvcSchemaManager — fetches OpenAPI, resolves $refs, annotates UI hints
```

The schema manager dynamically:
- Fetches `StartRunRequest` from LVC's `/openapi.json` at runtime
- Resolves `$ref` and `anyOf` wrappers
- Adds UI annotations (`x-vms-source`, `x-format`, `x-hidden`, `x-synthetic`)
- Builds a live Pydantic model for payload validation

### LVC Dashboard Integration

The operator dashboard renders LVC parameters dynamically from the live schema.

**Visible input fields (matching LVC's own UI):**

| Field | Control | Default |
|---|---|---|
| Camera | Dropdown (enabled cameras by name) | — |
| Enter Prompt | Textarea | "Describe what you see in one sentence." |
| Select Model | Dropdown from LVC API | OpenGVLab/InternVL2-2B |
| Max New Tokens | Number | 70 |
| Select Pipeline | Dropdown from LVC API | — |
| Run Name | Text | — |
| Frame Rate | Number | 1 |
| Chunk Size | Number | 1 |
| Frame Resolution | Dropdown: default / 1280×720 / 640×480 / 480×360 | default |

**Camera field**: The UI shows camera **names** only. The backend resolves the
selected `camera_id` to its RTSP `stream_url` before forwarding to LVC.

**Frame Resolution**: Maps to `frameWidth`/`frameHeight` integers before sending
to LVC — `1280x720 → {frameWidth:1280, frameHeight:720}`.

### Result Streaming

| Channel | Flow |
|---|---|
| **Captions** | LVC → MQTT → LVC SSE → Plugin proxy `/v1/core-apps/live_captioning/results/stream` → UI |
| **Video** | LVC → MediaMTX WebRTC → nginx `/whep/` proxy → WebRTC player |

---

## API Reference

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/health` | Liveness probe |
| `GET` | `/v1/ready` | Readiness (DB + VMS + Core App checks) |
| `GET` | `/v1/config/status` | Loaded config + uptime |

### Cameras

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/cameras` | List all persisted cameras |
| `GET` | `/v1/cameras/{camera_id}` | Get a single camera |
| `POST` | `/v1/cameras/discover` | Sync cameras from all VMS shims |
| `POST` | `/v1/cameras/enable` | Enable / disable a camera |
| `GET` | `/v1/cameras/{camera_id}/live-stream` | Get live RTSP stream URL |
| `GET` | `/v1/cameras/{camera_id}/clip` | Get clip URL for a time range |

### Generic Core App API

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/core-apps/discover` | List all registered Core Apps with schema |
| `GET` | `/v1/core-apps/{app_id}/schema` | Get live JSON Schema for start params |
| `POST` | `/v1/core-apps/{app_id}/runs` | Start a pipeline run |
| `GET` | `/v1/core-apps/{app_id}/runs` | List active runs |
| `GET` | `/v1/core-apps/{app_id}/runs/{run_id}` | Get run status |
| `DELETE` | `/v1/core-apps/{app_id}/runs/{run_id}` | Stop a run |
| `GET` | `/v1/core-apps/{app_id}/results/stream` | SSE proxy of live results |
| `GET` | `/v1/core-apps/{app_id}/options/{option_type}` | Dropdown options (models, pipelines) |

Currently registered app: `live_captioning`

### Events & Sessions

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/events/timeline` | Paginated metadata-event timeline |
| `POST` | `/v1/analysis/results` | Async result callback from Core App |
| `GET` | `/v1/sessions` | List analytics sessions |
| `GET` | `/v1/sessions/{session_id}` | Get session details |
| `POST` | `/v1/vms/{name}/register` | Register a VMS with the plugin |

---

## Local Development

### Backend

```bash
pip install -e ".[dev]"
export VMS_PLUGIN_CONFIG_PATH=$PWD/config/config.yaml
uvicorn plugin.core.main:app --reload --port 8082
pytest tests/ -v
```

### Frontend

```bash
cd ui
npm install
npm run dev       # http://localhost:5173 — proxies /v1 to backend
npm run build
```

---

## Adding a New Core App

1. Create `core_app_shim/<your_app>/shim.py` implementing `ICoreAppShim`:
   - `app_id` / `display_name` class attributes
   - `fetch_schema()` — return JSON Schema for start params
   - `start(params)` — start a run, return run metadata dict
   - Override `list_runs`, `stop_run`, `get_run`, `results_stream_url`, `get_options` as needed

2. Register the shim in `plugin/core/factory.py`.

3. No route changes needed — the generic `/v1/core-apps/{app_id}/…` routes handle everything.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn, SQLAlchemy 2 (async), PostgreSQL 15, Pydantic v2, httpx, structlog |
| Frontend | React 19, Vite, Tailwind CSS 4, shadcn/ui, Lucide icons |
| VMS | Frigate 0.15 (go2rtc RTSP), Nx Witness REST v4 |
| AI | Intel Live Video Captioning (DLStreamer + VLM), MediaMTX (WebRTC), MQTT |
| Infra | Docker Compose (4 services: backend, ui/nginx, postgres, frigate) |

