# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Milestone XProtect VMS shim, via **Milestone AI Bridge**.

AI Bridge is a free MIP SDK shipped as Linux OCI containers that sits beside a
Windows XProtect installation and mediates between the VMS and Intelligent Video
Analytics (IVA) applications. This shim registers VAP's Analytics Apps with it.

Implemented here:

* ``connect``            - GraphQL ``about { videoManagementSystems { id } }``
  probe; caches **every** attached XProtect system id.
* ``discover_cameras``   - GraphQL ``cameras { name videoStreams { id name
  streamAvailability { rtsp } } }``. AI Bridge returns a ready-to-use live RTSP
  URL per stream, so no URL construction is needed (unlike the Nx shim).
* ``get_live_stream_url``- cached/refreshed stream URL for a camera.
* ``on_startup``         - **auto-registers every configured Analytics App** as
  its own IVA app, with one ``register`` mutation per discovered XProtect system.
* ``register_analytics`` - the underlying registration call, also reachable via
  ``POST /v1/vms/{name}/register``.

Auto-registration
-----------------
Each Analytics App listed under ``analytics_apps`` in ``config.yaml`` becomes a
separate app in Management Client, so operators enable them independently. The
app's ``type`` selects its AI Bridge topic:

* ``object_detection`` (Loitering Detection) → metadata topic, ONVIF frame format
* ``live_captioning``                        → event topic, analytics events

App GUIDs are stable across restarts, so re-registering updates the existing app
instead of duplicating it.

Identifiers
-----------
AI Bridge addresses video by the composite ``"{deviceId}/{streamId}"``. Camera
IDs are therefore ``"milestone:{deviceId}/{streamId}"``.

Not available through AI Bridge (see the how-to guide for the workarounds):

* **Bookmarks** - no API; requires the SOAP ``ServerCommandService`` on the
  Windows management server.
* **Clip / export URL** - playback is a gRPC ``DirectStreaming.PlaybackStream``
  frame stream on ``:9898``; there is no MP4 URL to hand out.
* **Trigger recording** - no direct API; raise an analytics event and bind it to
  a recording rule in Management Client.

These return ``unsupported`` results rather than failing silently.

Configuration (``vendor_options`` in ``config.yaml``)::

    graphql_url:    http://aib-aibridge-webservice:4000/api/bridge/graphql
    dashboard_url:  https://<vap-host>:3443   # VAP UI, rendered by Mgmt Client
    vms_id:         <optional; every attached system is used when omitted>
    app_ids:        {dls_vision: <guid>, live_captioning: <guid>}   # optional
    metadata_format, app_version, manufacturer_name, manifest_path  # optional

References: https://doc.milestonesys.com/AIB/Help/latest/en-us/ and the official
samples at https://github.com/milestonesys/MIP-AIBridge-samples
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from plugin.base.interfaces import IVmsShim
from plugin.core.config import VmsInstanceConfig
from plugin.core.models.domain import Camera, CommandResult
from vms_shim.milestone import queries
from vms_shim.milestone.manifest import (
    TYPE_TOPIC_MAP,
    apps_literal,
    build_apps,
    load_manifest,
    topic_name,
    validated_metadata_format,
)
from vms_shim.milestone.onvif_metadata import translate_dls_to_onvif

if TYPE_CHECKING:
    from plugin.core.pipeline.orchestrator import Orchestrator

logger = structlog.get_logger(__name__)

CAMERA_ID_PREFIX = "milestone:"
DEFAULT_GRAPHQL_PATH = "/api/bridge/graphql"


class MilestoneVmsShim(IVmsShim):
    """Single shim for Milestone XProtect fronted by AI Bridge."""

    def __init__(self, config: VmsInstanceConfig):
        self._config = config
        self._options: dict[str, Any] = dict(config.vendor_options or {})
        self._client: httpx.AsyncClient | None = None
        self._connected = False
        # Every XProtect system behind this AI Bridge. Registration targets all
        # of them unless vendor_options.vms_id pins a single one.
        configured = self._options.get("vms_id")
        self._vms_ids: list[str] = [str(configured)] if configured else []
        # camera_id -> RTSP URL, refreshed on every discovery.
        self._stream_urls: dict[str, str] = {}
        # (topic, is_metadata) -> REST sink URL, resolved lazily on first push.
        self._topic_sinks: dict[tuple[str, bool], str] = {}
        # Set in on_startup; lets POST /v1/vms/{name}/register re-register the
        # currently configured Analytics Apps.
        self._orchestrator: Orchestrator | None = None

    # -- Configuration helpers -----------------------------------------
    @property
    def camera_id_prefix(self) -> str:
        return CAMERA_ID_PREFIX

    @property
    def _vms_id(self) -> str | None:
        """First known XProtect system id, or ``None`` when none are known."""
        return self._vms_ids[0] if self._vms_ids else None

    @property
    def _graphql_url(self) -> str:
        """Absolute GraphQL endpoint of the AI Bridge web service."""
        explicit = str(self._options.get("graphql_url") or "").strip()
        if explicit:
            return explicit
        base = (self._config.base_url or "").rstrip("/")
        return f"{base}{DEFAULT_GRAPHQL_PATH}" if base else ""

    def _httpx_verify(self) -> bool | str:
        if self._config.tls_verify and self._config.tls_ca_bundle:
            return self._config.tls_ca_bundle
        return self._config.tls_verify

    # -- Lifecycle ------------------------------------------------------
    async def connect(self) -> None:
        await self.disconnect()
        url = self._graphql_url
        if not url:
            logger.error("milestone_connect_failed", reason="graphql_url_not_configured")
            return

        self._client = httpx.AsyncClient(timeout=30.0, verify=self._httpx_verify())
        data = await self._graphql(queries.QUERY_VMS_IDS)
        if data is None:
            logger.error("milestone_connect_failed", graphql_url=url)
            await self.disconnect()
            return

        systems = (data.get("about") or {}).get("videoManagementSystems") or []
        discovered = [str(s["id"]) for s in systems if s.get("id")]
        # A pinned vms_id wins; otherwise target every attached system.
        if not self._options.get("vms_id"):
            self._vms_ids = discovered
        self._connected = True
        logger.info(
            "milestone_connected",
            graphql_url=url, vms_ids=self._vms_ids, discovered=len(discovered),
        )
        if not self._vms_ids:
            logger.warning(
                "milestone_no_vms_attached",
                detail="AI Bridge reports no XProtect system; registration will be skipped",
            )


    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        self._stream_urls.clear()
        # Sinks are tied to a registration; force re-resolution on reconnect.
        self._topic_sinks.clear()
        if not self._options.get("vms_id"):
            self._vms_ids = []

    def is_connected(self) -> bool:
        return self._connected

    async def _graphql(
        self, query: str, variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """POST a GraphQL document and return its ``data``, or ``None`` on failure.

        GraphQL reports errors inside a 200 response, so the ``errors`` key is
        checked explicitly rather than relying on the HTTP status alone.
        """
        if not self._client:
            return None
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        try:
            resp = await self._client.post(
                self._graphql_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            body = resp.json() or {}
        except (httpx.HTTPError, ValueError) as e:
            logger.error("milestone_graphql_failed", error=str(e))
            return None

        if body.get("errors"):
            logger.error("milestone_graphql_errors", errors=body["errors"])
            return None
        return body.get("data") or {}

    # -- Discovery / metadata ------------------------------------------
    async def discover_cameras(self) -> list[Camera]:
        data = await self._graphql(queries.QUERY_CAMERAS)
        if data is None:
            return []

        cameras: list[Camera] = []
        self._stream_urls.clear()
        for cam in data.get("cameras") or []:
            cam_name = cam.get("name") or ""
            for stream in cam.get("videoStreams") or []:
                stream_id = stream.get("id")
                if not stream_id:
                    continue
                rtsp = (stream.get("streamAvailability") or {}).get("rtsp")
                camera_id = f"{CAMERA_ID_PREFIX}{stream_id}"
                if rtsp:
                    self._stream_urls[camera_id] = rtsp
                device_id, _, native_stream_id = stream_id.partition("/")
                stream_name = stream.get("name") or ""
                cameras.append(Camera(
                    camera_id=camera_id,
                    name=f"{cam_name} - {stream_name}".strip(" -") or stream_id,
                    vendor="milestone",
                    # AI Bridge only lists streams it can serve; a stream without
                    # an RTSP endpoint is not usable for analytics.
                    status="online" if rtsp else "offline",
                    stream_url=rtsp,
                    enabled=False,
                    vendor_meta={
                        "device_id": device_id,
                        "stream_id": native_stream_id,
                        "camera_name": cam_name,
                        "stream_name": stream_name,
                    },
                ))
        logger.info("milestone_cameras_discovered", count=len(cameras))
        return cameras

    async def get_camera_metadata(self, camera_id: str) -> Camera | None:
        cams = await self.discover_cameras()
        return next((c for c in cams if c.camera_id == camera_id), None)

    # -- Stream / clip URLs --------------------------------------------
    async def get_live_stream_url(self, camera_id: str) -> str | None:
        """Return the AI Bridge RTSP URL (``rtsp://<aib>:8554/{device}/{stream}``)."""
        cached = self._stream_urls.get(camera_id)
        if cached:
            return cached
        cam = await self.get_camera_metadata(camera_id)
        return cam.stream_url if cam else None

    async def get_clip_url(
        self, camera_id: str, from_dt: datetime, to_dt: datetime,
    ) -> str | None:
        # AI Bridge exposes recorded video only as a gRPC frame stream
        # (DirectStreaming.PlaybackStream on :9898) — there is no clip/export URL
        # to return. Callers should treat None as "playback not addressable".
        logger.info(
            "milestone_clip_url_unsupported",
            camera_id=camera_id, hint="use gRPC DirectStreaming.PlaybackStream",
        )
        return None

    # -- IVA app registration ------------------------------------------
    async def on_startup(self, orchestrator: Orchestrator) -> None:
        """Auto-register every configured Analytics App as its own IVA app.

        Runs after :meth:`connect`, so the attached XProtect systems are already
        known. Failures are logged and swallowed: an unreachable AI Bridge must
        not stop VAP from serving its other VMS instances.
        """
        self._orchestrator = orchestrator
        apps = self._collect_analytics_apps(orchestrator)
        if not apps:
            logger.warning(
                "milestone_autoregister_skipped",
                vms=self._config.name,
                reason="no configured analytics app maps to an AI Bridge topic",
            )
            return

        result = await self.register_analytics({"analytics_apps": apps})
        if result.get("status") != "registered":
            logger.error(
                "milestone_autoregister_failed",
                vms=self._config.name, **{
                    k: v for k, v in result.items() if k != "status"
                },
            )
            return
        logger.info(
            "milestone_autoregister_complete",
            vms=self._config.name,
            app_ids=result.get("app_ids"),
            vms_ids=result.get("vms_ids"),
        )

    def _collect_analytics_apps(
        self, orchestrator: Orchestrator,
    ) -> list[dict[str, Any]]:
        """Read the configured Analytics Apps from ``analytics_apps`` in config.yaml.

        Each entry carries the ``type`` that selects its AI Bridge topic format
        (``object_detection`` → ONVIF frame metadata, ``live_captioning`` →
        analytics events). Apps whose type has no mapping are logged and skipped.
        """
        apps: list[dict[str, Any]] = []
        for cfg in getattr(orchestrator.config, "analytics_apps", []) or []:
            app_type = getattr(cfg, "type", "")
            app_id = getattr(cfg, "app_id", "") or app_type
            if app_type not in TYPE_TOPIC_MAP:
                logger.warning(
                    "milestone_unmapped_analytics_app_type",
                    app_id=app_id,
                    type=app_type,
                    known=sorted(TYPE_TOPIC_MAP),
                )
                continue
            apps.append({
                "app_id": app_id,
                "type": app_type,
                "display_name": getattr(cfg, "display_name", "") or app_id,
            })
        return apps

    async def register_analytics(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Register IVA apps with every attached XProtect system.

        Args:
            manifest: One of

                * ``{"analytics_apps": [...]}`` — VMS-neutral app descriptors
                  (``app_id``, ``display_name``, ``streams``), translated into AI
                  Bridge topics. This is what :meth:`on_startup` passes.
                * ``{"apps": [...]}`` — a ready-made AI Bridge manifest, used
                  verbatim.
                * anything else — falls back to the bundled static template.

        Returns a dict with ``status`` of ``registered`` or ``error``. A partial
        success (some XProtect systems registered, others not) is reported as
        ``registered`` with a non-empty ``failed_vms_ids``.
        """
        if not self._client or not self._connected:
            return {"status": "error", "reason": "not_connected"}
        if not self._vms_ids:
            return {"status": "error", "reason": "vms_id_unknown"}

        try:
            apps = self._resolve_apps(manifest)
        except (ValueError, OSError) as e:
            logger.error("milestone_manifest_invalid", error=str(e))
            return {"status": "error", "reason": str(e)}
        if not apps:
            return {"status": "error", "reason": "no_apps_to_register"}

        literal = apps_literal(apps)
        app_ids = [a["id"] for a in apps if a.get("id")]
        app_names = [a.get("name") for a in apps]

        registered: list[str] = []
        failed: list[str] = []
        for vms_id in self._vms_ids:
            data = await self._graphql(
                queries.build_register_mutation(vms_id, literal),
            )
            if data is None:
                failed.append(vms_id)
                logger.error("milestone_register_failed", vms_id=vms_id)
                continue
            registered.append(vms_id)
            logger.info(
                "milestone_iva_registered",
                vms_id=vms_id, apps=app_names, app_ids=app_ids,
            )

        if not registered:
            return {
                "status": "error",
                "reason": "register_mutation_failed",
                "failed_vms_ids": failed,
            }
        return {
            "status": "registered",
            "vms_ids": registered,
            "failed_vms_ids": failed,
            "app_ids": app_ids,
            "apps": app_names,
        }

    async def handle_register(
        self, body: dict[str, Any], db: Any, vms_name: str,
    ) -> Any:
        """Handle ``POST /v1/vms/{name}/register``.

        An explicit ``manifest`` in the body is honoured; otherwise the currently
        configured Analytics Apps are re-registered, which is the useful default
        after adding an app or changing the dashboard URL.
        """
        manifest = body.get("manifest") if isinstance(body, dict) else None
        if not manifest and self._orchestrator is not None:
            manifest = {
                "analytics_apps": self._collect_analytics_apps(self._orchestrator),
            }
        return await self.register_analytics(manifest or {})

    def _resolve_apps(self, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        """Turn any accepted manifest shape into a list of AI Bridge app entries."""
        if isinstance(manifest, dict):
            if manifest.get("apps"):
                return list(manifest["apps"])
            if manifest.get("analytics_apps"):
                return build_apps(
                    self._config.name, manifest["analytics_apps"], self._options,
                )
        return list(
            load_manifest(self._options, self._options.get("manifest_path"))["apps"],
        )

    async def get_topic_sink_url(
        self, topic: str, *, metadata: bool = True,
    ) -> str | None:
        """Resolve the HTTP sink an IVA app POSTs to for a registered topic.

        Only resolvable *after* :meth:`register_analytics` has succeeded.
        Results are cached: the sink is stable for the lifetime of a
        registration, and the push path would otherwise issue a GraphQL round
        trip per frame.
        """
        cache_key = (topic, metadata)
        if cache_key in self._topic_sinks:
            return self._topic_sinks[cache_key]

        query = (
            queries.QUERY_METADATA_TOPIC_REST if metadata
            else queries.QUERY_EVENT_TOPIC_REST
        )
        data = await self._graphql(query, {"topicName": topic})
        if data is None:
            return None
        topics = data.get("metadataTopics" if metadata else "eventTopics") or []
        if not topics:
            logger.warning("milestone_topic_not_found", topic=topic)
            return None
        sink = (topics[0].get("topicAvailability") or {}).get("rest")
        if sink:
            self._topic_sinks[cache_key] = sink
        return sink

    # -- Metadata / event push -----------------------------------------
    async def _post_to_topic(
        self, sink_url: str, body: str, content_type: str,
    ) -> bool:
        """POST a translated payload to a resolved topic sink."""
        if not self._client:
            return False
        try:
            resp = await self._client.post(
                sink_url,
                content=body.encode("utf-8"),
                headers={"Content-Type": content_type},
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error("milestone_topic_post_failed", error=str(e))
            return False

    async def push_detection_metadata(
        self,
        camera_id: str,
        payload: dict[str, Any],
        analytics_app_id: str,
        label_class_map: dict[str, str] | None = None,
        timestamp_offset_ms: int = 0,
    ) -> bool:
        """Translate DL Streamer metadata to ONVIF XML and push it to XProtect.

        Args:
            camera_id: VAP camera id (``milestone:{deviceId}/{streamId}``).
            payload: Raw DL Streamer MQTT metadata dict.
            analytics_app_id: Selects the registered topic for this app.
            label_class_map: Detection label → ONVIF class name.
            timestamp_offset_ms: Negative values compensate for inference
                latency so boxes align with the video frame.

        Returns True when XProtect accepted the payload.
        """
        topic = topic_name(analytics_app_id, TYPE_TOPIC_MAP["object_detection"]["suffix"])
        sink = await self.get_topic_sink_url(topic, metadata=True)
        if not sink:
            return False

        try:
            body = translate_dls_to_onvif(
                payload,
                source_stream_id=camera_id.removeprefix(CAMERA_ID_PREFIX),
                metadata_format=validated_metadata_format(self._options),
                label_class_map=label_class_map,
                timestamp_offset_ms=timestamp_offset_ms,
            )
        except (ValueError, TypeError, KeyError) as e:
            logger.error(
                "milestone_metadata_translation_failed",
                camera_id=camera_id, error=str(e),
            )
            return False

        return await self._post_to_topic(sink, body, "text/xml")

    async def push_analytics_event(
        self, camera_id: str, event: dict[str, Any], analytics_app_id: str,
    ) -> bool:
        """Push a translated ``ANALYTICS_EVENT`` document to XProtect.

        Build ``event`` with :mod:`vms_shim.milestone.analytics_event` — e.g.
        ``caption_to_event()`` for LVC or ``detections_to_event()`` for object
        detection.
        """
        spec = TYPE_TOPIC_MAP.get("live_captioning", {})
        topic = topic_name(analytics_app_id, spec.get("suffix", "events"))
        sink = await self.get_topic_sink_url(topic, metadata=False)
        if not sink:
            return False
        return await self._post_to_topic(
            sink, json.dumps(event), "text/json",
        )

    # -- Write-back operations -----------------------------------------
    async def acknowledge_event(
        self, camera_id: str, event_id: str, message: str = "",
    ) -> CommandResult:
        return _unsupported(
            "acknowledge_event", camera_id,
            "AI Bridge has no event acknowledgement API; acknowledge alarms in "
            "XProtect Smart Client or via the XProtect REST API",
        )

    async def set_bookmark(
        self, camera_id: str, timestamp: datetime, label: str,
    ) -> CommandResult:
        return _unsupported(
            "set_bookmark", camera_id,
            "AI Bridge exposes no bookmark API; use the SOAP ServerCommandService "
            "on the XProtect management server",
        )

    async def push_label(
        self, camera_id: str, event_id: str, labels: list[str],
        confidence: float | None = None,
    ) -> CommandResult:
        return _unsupported(
            "push_label", camera_id,
            "labels are carried inside ONVIF metadata / analytics event payloads, "
            "not pushed standalone",
        )

    async def trigger_recording(
        self, camera_id: str, duration_seconds: int = 30,
    ) -> CommandResult:
        return _unsupported(
            "trigger_recording", camera_id,
            "no direct API; raise an analytics event and bind it to a recording "
            "rule in XProtect Management Client",
        )


def _result(camera_id: str, ctype: str, status: str, msg: str) -> CommandResult:
    return CommandResult(
        command_id=str(uuid.uuid4()), camera_id=camera_id,
        command_type=ctype, status=status, vendor_message=msg,
    )


def _unsupported(ctype: str, camera_id: str, msg: str) -> CommandResult:
    return _result(camera_id, ctype, "unsupported", msg)
