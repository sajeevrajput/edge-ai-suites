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

# VMS Adapter Plugin

**VMS Adapter Plugin (VAP)** is an I/O bridge between VMS systems and AI Analytics Apps. It connects Frigate and Nx Witness cameras to AI analytics applications such as Live Video Captioning and Pallet Defect Detection, and presents a unified operator dashboard for managing cameras and analytics runs.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        VMS Adapter Plugin                           │
│                                                                     │
│  ┌──────────┐    ┌─────────────────┐    ┌──────────────────────┐   │
│  │ Frigate  │    │  FastAPI Backend │    │  Live Video          │   │
│  │ (VMS)   ├───►│  (plugin/)       ├───►│  Captioning (LVC)    │   │
│  └──────────┘    │                 │    │  Analytics App            │   │
│  ┌──────────┐    │  - Camera sync  │    └──────────────────────┘   │
│  │Nx Witness│    │  - Generic runs │    ┌──────────────────────┐   │
│  │ (VMS)   ├───►│  - Result proxy │    │  Pallet Defect       │   │
│  └──────────┘    └────────┬────────┘    │  Detection (PDD)     │   │
│                           │             │  Analytics App            │   │
│                  ┌────────▼────────┐    └──────────────────────┘   │
│                  │   React UI      │                                │
│                  │   (nginx)       │                                │
│                  └─────────────────┘                                │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Features

**Multi-VMS Support**: Connect cameras from Frigate and Nx Witness simultaneously from a single plugin instance.

**Pluggable Analytics Apps**: AI analytics applications plug in as shims. New apps require no route changes — just a new shim class.

**Live Video Captioning Integration**: Generate real-time AI captions from camera streams using DLStreamer and Vision Language Models.

**Pallet Defect Detection Integration**: Stream camera feeds to a DLStreamer Pipeline Server for warehouse defect detection, with bounding-box results pushed back into Nx Witness as analytics objects.

**Operator Dashboard**: React-based UI for discovering cameras, enabling/disabling streams, configuring analytics parameters, and viewing live results.

**Generic Analytics App API**: A single set of REST routes (`/v1/analytics-apps/{app_id}/…`) handles all AI analytics integrations with a consistent lifecycle (start, list, stop, stream results).

**Dynamic Schema Forms**: The dashboard renders analytics configuration forms directly from each Analytics App's live OpenAPI schema — no UI changes needed when parameters change.

## Use Cases

**Intelligent Surveillance**: Connect IP cameras from Nx Witness to Live Video Captioning for scene description and prompt-driven monitoring (for example, "Is there an unauthorized person in the area?").

**Warehouse Quality Control**: Route camera feeds from Frigate or Nx Witness to Pallet Defect Detection and automatically push detected defect bounding boxes back into Nx Witness for operator review.

**Multi-Camera Analytics Management**: Discover all cameras from all connected VMS systems in one dashboard and selectively enable AI analytics on specific cameras without reconfiguring each system individually.

## Learn More

- [Get Started](./get-started.md)
- [System Requirements](./get-started/system-requirements.md)
- [How It Works](./how-it-works.md)
- [How-To Guides](./how-to-guides.md)
- [Troubleshooting](./troubleshooting.md)
- [Release Notes](./release-notes.md)

<!--hide_directive
:::{toctree}
:hidden:

get-started
how-it-works
how-to-guides
troubleshooting
release-notes

:::
hide_directive-->
