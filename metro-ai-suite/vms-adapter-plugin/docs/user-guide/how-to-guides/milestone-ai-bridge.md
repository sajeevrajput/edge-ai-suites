# Milestone XProtect via Milestone AI Bridge

This guide explains how the VMS Adapter Plugin (VAP) integrates with **Milestone
XProtect** through **Milestone AI Bridge**, how to configure it, and which
capabilities are and are not available on this path.

> **Status:** camera discovery and automatic IVA app registration are implemented.
> Detection metadata push, playback and bookmarks are not yet implemented —
> see [Current limitations](#current-limitations).

## What Milestone AI Bridge is

Milestone AI Bridge is a free MIP SDK delivered as **Linux OCI containers**. It runs
on a separate Linux host (the *processing server*), connects outbound to the Windows
XProtect Management Server, and mediates in both directions between XProtect and
Intelligent Video Analytics (IVA) applications.

VAP registers each of its Analytics Apps — Loitering Detection, Live Video
Captioning — with AI Bridge as a separate **IVA app**, discovers cameras over
GraphQL, and consumes live video over RTSP.

```
┌──────────────────┐        ┌──────────────────────┐        ┌─────────────────┐
│ XProtect VMS     │◄──────►│ Milestone AI Bridge  │◄──────►│ VMS Adapter     │
│ (Windows)        │        │ (Linux containers)   │        │ Plugin (IVA)    │
│  Mgmt Server     │        │  GraphQL   :4000     │ GraphQL│                 │
│  Recording Srv   │        │  RTSP      :8554     │  RTSP  │  Analytics Apps │
│  Event Server    │        │  gRPC DS   :9898     │        │                 │
└──────────────────┘        └──────────────────────┘        └─────────────────┘
```

Interfaces VAP uses:

| Purpose | Interface |
| --- | --- |
| Control plane (discovery, registration, topic lookup) | GraphQL — `POST http://<aib-host>:4000/api/bridge/graphql` |
| Live video | RTSP — `rtsp://<aib-host>:8554/{deviceId}/{streamId}` |
| Recorded video | gRPC `DirectStreaming.PlaybackStream` on `:9898` (not yet used by VAP) |
| Detection metadata / events | HTTP POST to per-topic sinks discovered via GraphQL |

## Prerequisites

1. **XProtect 2022 R1 or later.** 2022 R1/R2 require a Milestone patch; 2022 R3+
   works as shipped.
2. The **Milestone XProtect Processing Server Admin Plugin**
   (`VideoOS.ProcessingServer.Plugin.Admin.Installer.exe`) installed into
   XProtect Management Client.
3. **AI Bridge deployed and attached to XProtect** on a Linux host (Docker Compose
   or Helm). Images: <https://gallery.ecr.aws/milestonesys/aibridge>.
4. Network reachability **in both directions**: VAP must reach the AI Bridge GraphQL
   and RTSP ports, and the XProtect host must be able to resolve and reach the
   `app_base_url` VAP registers.

> **Licensing:** AI Bridge itself is free, and pushing *events* and *metadata* costs
> nothing. Pushing *video* back into XProtect consumes one XProtect device license
> per stream — VAP therefore registers **no** `videoTopics`.

## Configuration

Set the AI Bridge variables in `.env`:

```bash
AIB_HOST=<ai-bridge-ip>
AIB_GRAPHQL_PORT=4000
AIB_TLS_VERIFY=false
AIB_CA_BUNDLE=
# How the AI Bridge and XProtect hosts reach THIS plugin
AIB_APP_HOSTNAME=<vms-adapter-host>
AIB_APP_PORT=8080
```

Then uncomment the `milestone-main` block in `config/config.yaml`:

```yaml
vms_instances:
  - name: milestone-main
    vendor: milestone
    base_url: "http://${AIB_HOST}:${AIB_GRAPHQL_PORT:-4000}"
    tls_verify: ${AIB_TLS_VERIFY:-false}
    tls_ca_bundle: "${AIB_CA_BUNDLE:-}"
    vendor_options:
      graphql_url: "http://${AIB_HOST}:${AIB_GRAPHQL_PORT:-4000}/api/bridge/graphql"
      dashboard_url: "https://${AIB_APP_HOSTNAME}:${UI_HTTPS_PORT:-3443}"
      metadata_format: ONVIF_ANALYTICS_FRAME
```

No Milestone-specific configuration is needed for the analytics apps themselves —
whatever is listed under `analytics_apps:` is registered automatically.

### `vendor_options` reference

| Key | Required | Default | Description |
| --- | --- | --- | --- |
| `graphql_url` | no | `<base_url>/api/bridge/graphql` | AI Bridge GraphQL endpoint. |
| `dashboard_url` | **yes** | — | VAP dashboard URL (the UI, HTTPS on 3443). Management Client renders it as each app's configuration page, so it must resolve **from the XProtect host**. `app_base_url` is accepted as a legacy alias. |
| `vms_id` | no | all discovered | Pin a single XProtect system. When omitted, VAP registers with **every** system attached to the AI Bridge. |
| `app_ids` | no | derived | Map of analytics app id → GUID, e.g. `{dls_vision: <guid>}`. Derived deterministically when omitted. |
| `metadata_format` | no | `ONVIF_ANALYTICS_FRAME` | Format for `object_detection` metadata topics. One of `ONVIF_ANALYTICS`, `ONVIF_ANALYTICS_FRAME`, `DEEPSTREAM_MINIMAL`. Does not affect event topics. |
| `app_version`, `manufacturer_name` | no | `1.0`, `Intel Corporation` | Shown in Management Client. |
| `manifest_path` | no | bundled `aib_manifest.json` | Static manifest template, used only when no configured analytics app maps to a topic. |

`ONVIF_ANALYTICS_FRAME` is the default because it is standards-based;
`DEEPSTREAM_MINIMAL` is NVIDIA DeepStream's pipe-delimited format and is only
appropriate if your pipeline already emits that shape.

## How it works

### Connection and VMS discovery

On startup the shim POSTs `query { about { videoManagementSystems { id } } }`. This
doubles as a reachability probe and yields the ids of **every** XProtect system
attached to the AI Bridge. Registration targets all of them, matching Milestone's
own sample behaviour. Set `vendor_options.vms_id` to pin exactly one.

### Camera discovery

```graphql
query { cameras { name videoStreams { id name streamAvailability { rtsp } } } }
```

AI Bridge addresses video by the composite `"{deviceId}/{streamId}"`, so VAP camera
IDs are `milestone:{deviceId}/{streamId}`. Both halves are also stored in
`vendor_meta` as `device_id` and `stream_id`.

Unlike the Nx Witness shim, VAP does **not** construct RTSP URLs — AI Bridge returns
a ready-to-use `streamAvailability.rtsp` value. A stream without an RTSP endpoint is
reported as `offline`.

### Auto-registration of Analytics Apps

After connecting, VAP registers **each app in the `analytics_apps` list as its own
IVA app**, so Loitering Detection and Live Video Captioning appear as separate
entries in Management Client and operators enable them independently.

The flow is:

1. Read the `analytics_apps` list from `config.yaml`.
2. Look at each app's `type` to pick its AI Bridge topic and format.
3. Emit one `register` mutation per discovered XProtect system.

The `type` → topic mapping is:

| Analytics app `type` | AI Bridge topic | Format | Example app |
| --- | --- | --- | --- |
| `object_detection` | `metadataTopics` | `ONVIF_ANALYTICS_FRAME` | Loitering Detection |
| `live_captioning` | `eventTopics` | `ANALYTICS_EVENT` | Live Video Captioning |

Object detection produces bounding boxes, which XProtect ingests as metadata — this
is what drives box overlays and Metadata Search. Captions are free text with no
boxes, so they are delivered as discrete analytics events instead.

Topic names are prefixed with the app id (`dls_vision-metadata`,
`live_captioning-events`) so several apps can share one AI Bridge without
colliding. Video topics are never declared: each consumes one XProtect device
license per stream.

An app whose `type` has no mapping is logged as
`milestone_unmapped_analytics_app_type` and skipped, so adding a new analytics app
type cannot break registration for the others. Extend `TYPE_TOPIC_MAP` in
[`vms_shim/milestone/manifest.py`](../../../vms_shim/milestone/manifest.py) to
support one.

### App GUIDs and idempotency

AI Bridge identifies an app by GUID. VAP derives one deterministically from the VMS
instance name and the analytics app id
(`uuid5(namespace, "milestone-main:dls_vision")`), so restarting VAP **updates** the
existing app instead of creating a duplicate. Scoping by VMS instance name means two
VAP deployments can share one AI Bridge without clashing.

Pin GUIDs explicitly when you need to adopt apps registered by something else:

```yaml
vendor_options:
  app_ids:
    dls_vision: "11111111-2222-3333-4444-555555555555"
```

### Manual re-registration

Auto-registration runs at startup. To re-run it after changing configuration:

```bash
curl -X POST http://<vap-host>:8080/v1/vms/milestone-main/register \
  -H 'Content-Type: application/json' -d '{}'
```

An empty body re-registers the currently configured analytics apps. Supplying
`{"manifest": {"apps": [...]}}` sends a hand-written AI Bridge manifest verbatim.

After registration, the topics appear per camera in Management Client under
**Recording Servers → *camera* → Processing server**, where an operator ticks the
topics to subscribe to.

## Capability matrix

| Capability | Supported | Notes |
| --- | --- | --- |
| Discover cameras | ✅ | GraphQL `cameras` query |
| Discover XProtect systems | ✅ | `about { videoManagementSystems { id } }` |
| Live stream URL | ✅ | RTSP from `streamAvailability.rtsp` |
| Auto-register analytics apps | ✅ | One IVA app per analytics app, per XProtect system |
| Resolve topic sinks | ✅ | `get_topic_sink_url()` |
| Push detection metadata | ⏳ | Topic sink resolution is in place; the ONVIF payload builder is not yet implemented |
| Recorded clip URL | ❌ | AI Bridge serves playback only as a gRPC frame stream — there is no clip/MP4 URL. Returns `None`. |
| Bookmarks | ❌ | No AI Bridge API. Requires SOAP `ServerCommandService` on the XProtect management server. |
| Acknowledge event | ❌ | Acknowledge alarms in Smart Client or via the XProtect REST API. |
| Push label | ❌ | Labels are carried inside metadata/event payloads, not pushed standalone. |
| Trigger recording | ❌ | No direct API. Raise an analytics event and bind it to a recording rule in Management Client. |

Unsupported write-backs return a `CommandResult` with `status: "unsupported"` and an
explanatory `vendor_message` — they never fail silently.

## Current limitations

* **Detection metadata push is not implemented yet.** Apps are registered and their
  topic sinks are resolvable, but the ONVIF frame XML builder is not written. Note
  that ONVIF bounding boxes use normalized **−1…+1** coordinates, unlike the 0…1
  convention elsewhere in VAP.
* **The dashboard is not app-aware yet.** Registered `url`s carry `?app=<id>` and
  `?app=<id>&topic=<stream>` query parameters, but the VAP UI does not yet read them
  to open a specific app's view.
* **No OAuth token validation.** Production AI Bridge deployments set
  `enforce-oauth=true`, which requires an IVA app to validate operator JWTs against
  the XProtect IDP and forward them upstream. Not yet implemented.
* **Debug vs production endpoints.** Ports 4000/8554/9898 are exposed directly only
  in AI Bridge debug mode; production deployments route through the proxy or an
  nginx gateway. Set `graphql_url` accordingly.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `milestone_connect_failed` with `graphql_url_not_configured` | Neither `base_url` nor `vendor_options.graphql_url` is set. |
| `milestone_connect_failed` | AI Bridge unreachable, or the GraphQL port is not exposed (production mode routes through the proxy). |
| `milestone_graphql_errors` | The query reached AI Bridge but was rejected. Inspect the logged `errors`; verify the AI Bridge version against `:4000/voyager` in debug mode. |
| `milestone_no_vms_attached` | AI Bridge is running but no XProtect system is attached to it. |
| `milestone_autoregister_skipped` | No configured analytics app maps to an AI Bridge topic — check `analytics_apps:` in `config.yaml`. |
| `milestone_unmapped_analytics_app_type` | An analytics app's `type` has no topic mapping; extend `TYPE_TOPIC_MAP` in `vms_shim/milestone/manifest.py`. |
| `milestone_autoregister_failed` with `dashboard_url is required` | Add `dashboard_url` to `vendor_options`. |
| `failed_vms_ids` non-empty | Registration succeeded for some XProtect systems but not others; the per-system `milestone_register_failed` log line names them. |
| Apps registered, but topics do not appear in Management Client | The Processing Server Admin Plugin is not installed, or `dashboard_url` is not resolvable from the XProtect host. |
| Duplicate apps in Management Client | A `vendor_options.app_ids` entry changed, or the VMS instance was renamed — both change the app GUID. Remove the stale apps in Management Client. |
| No cameras discovered | The AI Bridge user lacks camera permissions, or the AI Bridge `zone`/`scope` configuration excludes them. |

## References

* Milestone AI Bridge documentation — <https://doc.milestonesys.com/AIB/Help/latest/en-us/>
* Official samples — <https://github.com/milestonesys/MIP-AIBridge-samples>
* AI Bridge container images — <https://gallery.ecr.aws/milestonesys/aibridge>
