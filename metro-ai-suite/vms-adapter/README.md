# VMS Adapter Plugin: AI Analytics Bridge for VMS Systems

The VMS Adapter Plugin (VAP) serves as an I/O bridge between Video Management Systems (VMS)
such as Frigate and Nx Witness, and AI Analytics Apps such as Live Video Captioning and Pallet
Defect Detection. It combines a FastAPI backend, pluggable VMS and Analytics App shims, and a
React operator dashboard into a single Docker Compose deployment.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        VMS Adapter Plugin                           │
│                                                                     │
│  ┌──────────┐    ┌─────────────────┐    ┌──────────────────────┐    │
│  │ Frigate  │    │ FastAPI Backend │    │  Live Video          │    │
│  │ (VMS)    ├───►│ (plugin/)       ├───►│  Captioning (LVC)    │    │
│  └──────────┘    │                 │    │  Analytics App       |    │   
│  ┌──────────┐    │  - Camera sync  │    └──────────┬───────────┘    │
│  │Nx Witness│    │  - Generic runs │               │                │
│  │ (VMS)    ├───►│  - Result proxy │    ┌──────────▼───────────┐    │
│  └──────────┘    └────────┬────────┘    │  MediaMTX (WebRTC)   │    │
│                           │             │  MQTT Broker         │    │
│                  ┌────────▼────────┐    └──────────────────────┘    │
│                  │   React UI      │                                │
│                  │   (nginx)       │                                │
│                  └─────────────────┘                                │
└─────────────────────────────────────────────────────────────────────┘
```

## Documentation

- **Overview**
  - [Overview](./docs/user-guide/index.md): A high-level introduction.
  - [How It Works](./docs/user-guide/how-it-works.md): Architecture, data flows, and component
    descriptions.

- **Getting Started**
  - [Get Started](./docs/user-guide/get-started.md): Step-by-step guide to deploy and run the
    application.
  - [System Requirements](./docs/user-guide/get-started/system-requirements.md): Hardware and
    software requirements for running the application.
  - [How-To Guides](./docs/user-guide/how-to-guides.md): End-to-end tutorials for LVC and PDD
    integrations.
  - [Troubleshooting](./docs/user-guide/troubleshooting.md): Support and troubleshooting
    information.

- **Deployment**
  - [Build from Source](./docs/user-guide/get-started/build-from-source.md): Instructions for
    building from source code.
  - [Deploy with Helm](./docs/user-guide/get-started/deploy-with-helm.md): Instructions for
    deploying with Helm.

- **API Reference**
  - [API Reference](./docs/user-guide/api-reference.md): Comprehensive reference for the
    available REST API endpoints.

- **Release Notes**
  - [Release Notes](./docs/user-guide/release-notes.md): Information on the latest updates,
    improvements, and bug fixes.
