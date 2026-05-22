<!--hide_directive
<div class="component_card_widget">
  <a class="icon_github" href="https://github.com/open-edge-platform/edge-ai-suites/tree/main/metro-ai-suite/vms-adapter">
     GitHub
  </a>
  <a class="icon_document" href="https://github.com/open-edge-platform/edge-ai-suites/blob/main/metro-ai-suite/vms-adapter/README.md">
     Readme
  </a>
</div>
hide_directive-->

# VMS Adapter Plugin Overview

The VMS Adapter Plugin (VAP) is an I/O bridge between Video Management Systems (VMS) and AI Analytics Apps. It is designed to help developers understand how to connect existing VMS infrastructure to AI analytics pipelines, manage camera streams through a unified operator dashboard, and extend the system with new VMS vendors or analytics applications.

## Overview

The **VMS Adapter Plugin** connects VMS solutions like Nx Witness, Genetec, Milestone, and Frigate cameras to AI analytics
applications such as Live Video Captioning and DLStreamer Vision based Loitering Detection, and presents a unified React operator dashboard for discovering cameras, managing analytics runs, and viewing live results. Adding support for a new VMS or a new Analytics App requires only a new shim class — no route changes are needed.

### Example Use Cases

- **Intelligent Surveillance**: Connect IP cameras from Nx Witness to Live Video Captioning for scene description and prompt-driven monitoring (for example, "Is there an unauthorized person in the area?").
- **Warehouse Quality Control**: Route camera feeds from Frigate or Nx Witness to DLStreamer Vision application and automatically push detected defect bounding boxes back into Nx Witness   for operator review.
- **Multi-Camera Analytics Management**: Discover all cameras from all connected VMS systems in one dashboard and selectively enable AI analytics on specific cameras without reconfiguring each system individually.

### Key Benefits

- **Multi-VMS Support**: Connect cameras from Nx Witness and Frigate simultaneously from a
  single plugin instance.
- **Pluggable Analytics Apps**: AI analytics applications plug in as shims. New apps require no route changes — just a new shim class registered in `factory.py`.
- **Dynamic Schema Forms**: The dashboard renders analytics configuration forms directly from each Analytics App's live OpenAPI schema — no frontend changes are needed when parameters change.
- **Generic Analytics App API**: A single set of REST routes (`/v1/analytics-apps/{app_id}/…`) handles all integrations with a consistent lifecycle (start, list, stop, stream results).
- **Operator Dashboard**: React-based UI for discovering cameras, enabling/disabling streams, configuring analytics parameters, and viewing live results.

## How it Works

The VAP is a modular orchestration service. VMS shims discover cameras from their respective systems and provide RTSP URLs. Analytics App shims manage run lifecycle and result delivery. The FastAPI backend coordinates between shims, persists state to PostgreSQL, and exposes a unified API consumed by the React operator dashboard.

```
VMS Systems
  ┌──────────┐   RTSP / REST    ┌───────────────────────────────────────────┐
  │ Any VMS  ├─────────────────►│                                           │
  └──────────┘                  │           VMS Adapter Plugin              │
  ┌──────────┐   RTSP / REST    │                                           │
  │Nx Witness├─────────────────►│  FastAPI Backend    ┌───────────────────┐ │
  └──────────┘                  │  ─────────────      │  PostgreSQL DB    │ │
                                │  Orchestrator   ◄──►│  (cameras,        │ │
                                │  Camera sync        │   sessions,       │ │
                                │  Schema fetch       │   events)         │ │
                                │                     └───────────────────┘ │
                                └────────┬─────────────────────┬────────────┘
                                         │                     │
                          ┌──────────────▼──────┐   ┌─────────▼──────────────┐
                          │  Live Video         │   │  Loitering Detetcion   │
                          │  Captioning (LVC)   │   │  (DLS vision) App      │
                          └──────────┬──────────┘   └────────────┬───────────┘
                                     │                           │
                          ┌──────────▼───────────────────────────▼─────────┐
                          │              Operator Dashboard (React)        │
                          │   Camera list | Run controls | Live stream     │
                          └────────────────────────────────────────────────┘
```

See [How It Works](./how-it-works.md) for a detailed breakdown of data flows, component
descriptions, and extension points.

### Key Features

- **Feature 1**: Multi-VMS architecture with a pluggable shim model enables adding new VMS
  vendors without modifying core routes.
- **Feature 2**: Connects to AI analytics pipelines — Live Video Captioning (DLStreamer + VLM)
  and Loitering Detection (DLStreamer Pipeline Server) — through the generic Analytics App
  shim interface.
- **Feature 3**: React operator dashboard dynamically renders analytics forms from each
  Analytics App's live OpenAPI schema, requiring no UI changes when app parameters evolve.
- **Feature 4**: DLStreamer Vision results are translated from DLStreamer GVA JSON
  format and pushed back to Nx Witness as analytics objects (bounding boxes with labels),
  visible directly in the Nx Witness Desktop Client.

## Learn More

- [Get Started](./get-started.md): Follow step-by-step instructions to deploy and run the
  application.
- [System Requirements](./get-started/system-requirements.md): Check the hardware and
  software requirements.
- [Build from Source](./get-started/build-from-source.md): Build and deploy the application
  from source using Docker Compose.
- [Deploy with Helm](./get-started/deploy-with-helm.md): Deploy the application with Helm.
- [How It Works](./how-it-works.md): Detailed architecture, data flows, and component
  descriptions.
- [How-To Guides](./how-to-guides.md): End-to-end tutorials for Live Video Captioning and
  DLStreamer Vision integrations.
- [API Reference](./api-reference.md): Comprehensive reference for the available REST API
  endpoints.
- [Troubleshooting](./troubleshooting.md): Find solutions to common issues.
- [Release Notes](./release-notes.md): Latest updates, improvements, and known issues.

<!--hide_directive
:::{toctree}
:hidden:

get-started
how-it-works
how-to-guides
api-reference
troubleshooting
release-notes

:::
hide_directive-->
