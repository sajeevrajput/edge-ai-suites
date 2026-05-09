"""Nx Witness VMS shim - single class, standard REST API v4 only.

All endpoints used here are documented in the official Nx Meta API tool
(https://meta.nxvms.com/doc/developers/api-tool):

  * POST   /rest/v4/login/sessions               -> create session token
  * DELETE /rest/v4/login/sessions/{token}       -> invalidate session
  * GET    /rest/v4/servers/*/info               -> reachability probe (returns array)
  * GET    /rest/v4/devices                      -> list devices
  * GET    /rest/v4/devices/{deviceId}           -> device record
  * GET    /{deviceId}                           -> RTSP live stream URL (constructed, not called)
  * POST   /rest/v4/devices/{deviceId}/bookmarks -> create bookmark
  * GET    /rest/v4/analytics/engines            -> list engines
  * PATCH  /rest/v4/devices/{deviceId}           -> toggle recording

Live RTSP URLs are constructed client-side per the Nx v4 spec
(``/{deviceId}`` Utilities endpoint):

  rtsp://<host>:<port>/{deviceId}?onvif_replay=true

The media server serves RTSP on the same host and port as the REST API.
Credentials are embedded in the URL for third-party RTSP client compatibility
(VLC, FFmpeg, etc.) since those require Basic/Digest auth. Clip URLs are
built using the documented ``/rest/v4/devices/{id}/footage`` endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from plugin.base.interfaces import IVmsShim
from plugin.core.config import NvrInstanceConfig
from plugin.core.models.domain import Camera, CommandResult

logger = structlog.get_logger(__name__)


class NxWitnessVmsShim(IVmsShim):
    """Single shim for Nx Witness using only standard /rest/v4 endpoints."""

    def __init__(self, config: NvrInstanceConfig):
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._connected = False
        self._token: str | None = None

    # -- Lifecycle ------------------------------------------------------
    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=30.0,
            verify=False,  # Nx ships self-signed certs by default
        )
        await self._login()

    async def _login(self) -> None:
        """POST /rest/v4/login/sessions to obtain a Bearer token."""
        if not self._client:
            return
        auth = self._config.auth
        if not auth.username:
            self._connected = False
            return
        try:
            resp = await self._client.post(
                "/rest/v4/login/sessions",
                json={"username": auth.username, "password": auth.password},
            )
            resp.raise_for_status()
            self._token = (resp.json() or {}).get("token")
            if self._token:
                self._client.headers["Authorization"] = f"Bearer {self._token}"
            # Probe reachability with a documented endpoint.
            info = await self._client.get("/rest/v4/servers/*/info")
            self._connected = info.status_code == 200
            logger.info("nx_connected", status=info.status_code)
        except httpx.HTTPError as e:
            logger.error("nx_connect_failed", error=str(e))
            self._connected = False

    async def disconnect(self) -> None:
        if self._client and self._token:
            try:
                await self._client.delete(f"/rest/v4/login/sessions/{self._token}")
            except httpx.HTTPError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        self._token = None

    def is_connected(self) -> bool:
        return self._connected

    # -- Discovery / metadata ------------------------------------------
    async def discover_cameras(self) -> list[Camera]:
        if not self._client:
            return []
        try:
            resp = await self._client.get("/rest/v4/devices")
            resp.raise_for_status()
            devices = resp.json()
        except httpx.HTTPError as e:
            logger.error("nx_discover_failed", error=str(e))
            return []

        cameras: list[Camera] = []
        for d in devices:
            if d.get("deviceType") != "Camera":
                continue
            cameras.append(_to_camera(d))
        logger.info("nx_cameras_discovered", count=len(cameras))
        return cameras

    async def get_camera_metadata(self, camera_id: str) -> Camera | None:
        if not self._client:
            return None
        device_id = camera_id.removeprefix("nx:")
        try:
            resp = await self._client.get(f"/rest/v4/devices/{device_id}")
            resp.raise_for_status()
            return _to_camera(resp.json())
        except httpx.HTTPError:
            return None

    # -- Stream / clip URLs --------------------------------------------
    async def get_live_stream_url(self, camera_id: str) -> str | None:
        """Build live RTSP URL per Nx Utilities with onvif_replay enabled."""
        device_id = camera_id.removeprefix("nx:")
        parsed = urlparse(self._config.base_url)
        host = parsed.hostname or self._config.base_url
        port = parsed.port or 7001
        auth = self._config.auth

        if auth.username:
            return f"rtsp://{auth.username}:{auth.password}@{host}:{port}/{device_id}?onvif_replay=true"    #TODO credentials in plain text. must hide it
        else:
            return f"rtsp://{host}:{port}/{device_id}?onvif_replay=true"

    async def get_clip_url(
        self, camera_id: str, from_dt: datetime, to_dt: datetime,
    ) -> str | None:
        # The standard Nx REST API does not expose a single "clip URL"
        # endpoint. Footage retrieval is handled by /rest/v4/devices/{id}
        # /footage which returns segment metadata; clients then fetch
        # via HLS. We surface that endpoint URL for the caller.
        if not self._client:
            return None
        device_id = camera_id.removeprefix("nx:")
        return (
            f"{self._config.base_url.rstrip('/')}"
            f"/rest/v4/devices/{device_id}/footage"
            f"?startTimeMs={int(from_dt.timestamp() * 1000)}"
            f"&endTimeMs={int(to_dt.timestamp() * 1000)}"
        )

    # -- Register analytics manifest -----------------------------------
    async def register_analytics(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Phase 1 Nx Analytics Integration registration.

        If ``manifest`` contains the structured Nx keys (``integrationManifest``,
        ``engineManifest``), the full Phase 1 REST workflow is executed:
          1. POST /rest/v4/analytics/integrations/*/requests
          2. POST .../requests/{requestId}/approve

        If the manifest is empty or missing those keys, falls back to listing
        existing engines (backward-compatible behaviour for non-Nx callers).
        """
        if not self._client:
            return {"status": "error", "reason": "not_connected"}

        integration_manifest = manifest.get("integrationManifest")
        engine_manifest = manifest.get("engineManifest")

        if not integration_manifest or not engine_manifest:
            # Backward-compat: just list available engines.
            try:
                resp = await self._client.get("/rest/v4/analytics/engines")
                return {
                    "status": "ok" if resp.status_code == 200 else "error",
                    "http_status": resp.status_code,
                    "engines": resp.json() if resp.status_code == 200 else None,
                }
            except httpx.HTTPError as e:
                return {"status": "error", "reason": str(e)}

        device_agent_manifest = manifest.get("deviceAgentManifest")
        pin_code = manifest.get("pinCode", "1234")

        payload: dict[str, Any] = {
            "integrationManifest": integration_manifest,
            "engineManifest": engine_manifest,
            "pinCode": pin_code,
            "isRestOnly": True,
        }
        if device_agent_manifest:
            payload["deviceAgentManifest"] = device_agent_manifest

        # Try fresh registration first
        fresh = await self._post_integration_request(payload)
        if fresh:
            approved = await self._approve_integration_request(fresh["request_id"])
            if not approved:
                return {
                    "status": "registered",
                    "username": fresh["username"],
                    "password": fresh["password"],
                    "request_id": fresh["request_id"],
                    "reason": "approval_failed",
                }
            logger.info(
                "nx_integration_approved",
                username=fresh["username"],
                request_id=fresh["request_id"],
            )
            return {
                "status": "approved",
                "username": fresh["username"],
                "password": fresh["password"],
                "request_id": fresh["request_id"],
            }

        return {"status": "error", "reason": "create_integration_request_failed"}

    async def _post_integration_request(
        self, payload: dict[str, Any],
    ) -> dict[str, str] | None:
        """Single attempt at POST /rest/v4/analytics/integrations/*/requests."""
        try:
            resp = await self._client.post(  # type: ignore[union-attr]
                "/rest/v4/analytics/integrations/*/requests",
                json=payload,
            )
            resp.raise_for_status()
            result = resp.json()
            return {
                "username": result.get("username", ""),
                "password": result.get("password", ""),
                "request_id": result.get("requestId", ""),
            }
        except httpx.HTTPStatusError as e:
            logger.error(
                "nx_create_integration_failed",
                status_code=e.response.status_code,
                response_body=e.response.text,
                payload_keys=list(payload.keys()),
            )
            return None
        except httpx.HTTPError as e:
            logger.error("nx_create_integration_failed", error=str(e))
            return None

    async def find_integration_in_vms(
        self, manifest_id: str,
    ) -> dict[str, str] | None:
        """Check whether an integration with the given manifest ID exists in Nx.

        Checks both the approved integrations list and the Nx users list (to
        catch pending/unapproved requests). Returns a dict with ``username``,
        ``password`` (empty — not recoverable from Nx), and ``request_id`` if
        found, or ``None`` if the integration does not exist in Nx at all.
        """
        # 1. Check approved integrations list
        try:
            resp = await self._client.get("/rest/v4/analytics/integrations")  # type: ignore[union-attr]
            resp.raise_for_status()
            for item in resp.json():
                api_info = item.get("apiIntegrationInfo") or {}
                sdk_info = item.get("sdkIntegrationInfo") or {}
                if (
                    api_info.get("integrationId") == manifest_id
                    or sdk_info.get("integrationId") == manifest_id
                ):
                    return {
                        "username": manifest_id,
                        "password": "",  # not recoverable from Nx
                        "request_id": api_info.get("integrationUserId") or item.get("id", ""),
                    }
        except httpx.HTTPError as e:
            logger.error("nx_list_integrations_failed", error=str(e))
            return None  # can't determine state — treat as unknown

        # 2. Check users list for a pending (unapproved) integration
        try:
            resp = await self._client.get("/rest/v4/users")  # type: ignore[union-attr]
            resp.raise_for_status()
            for user in resp.json():
                if user.get("name") == manifest_id or user.get("login") == manifest_id:
                    return {
                        "username": manifest_id,
                        "password": "",  # not recoverable from Nx
                        "request_id": user.get("id", ""),
                    }
        except httpx.HTTPError as e:
            logger.error("nx_list_users_failed", error=str(e))

        return None

    async def _approve_integration_request(self, request_id: str) -> bool:
        """POST /rest/v4/analytics/integrations/*/requests/{requestId}/approve."""
        try:
            resp = await self._client.post(  # type: ignore[union-attr]
                f"/rest/v4/analytics/integrations/*/requests/{request_id}/approve",
                json={"requestId": request_id},
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error("nx_approve_integration_failed", error=str(e), request_id=request_id)
            return False

    # -- Write-back -----------------------------------------------------
    async def acknowledge_event(
        self, camera_id: str, event_id: str, message: str = "",
    ) -> CommandResult:
        # Acknowledgement of analytics events is not part of the standard
        # /rest/v4 surface - it is plugin-specific in Nx. Return unsupported.
        return _unsupported("acknowledge_event", camera_id,
                            "Standard Nx v4 REST API has no event-acknowledgement endpoint")

    async def set_bookmark(
        self, camera_id: str, timestamp: datetime, label: str,
    ) -> CommandResult:
        if not self._client:
            return _unsupported("set_bookmark", camera_id, "Not connected")
        device_id = camera_id.removeprefix("nx:")
        try:
            resp = await self._client.post(
                f"/rest/v4/devices/{device_id}/bookmarks",
                json={
                    "name": label,
                    "description": f"VMS Plugin: {label}",
                    "startTimeMs": int(timestamp.timestamp() * 1000),
                    "durationMs": 30_000,
                },
            )
            return _result(camera_id, "set_bookmark",
                           "accepted" if resp.status_code in (200, 201, 204) else "rejected",
                           resp.text)
        except httpx.HTTPError as e:
            return _result(camera_id, "set_bookmark", "timeout", str(e))

    async def push_label(
        self, camera_id: str, event_id: str, labels: list[str],
        confidence: float | None = None,
    ) -> CommandResult:
        # The plugin maps labels to a bookmark - that is the only standard
        # storage surface available without an analytics engine plugin.
        return await self.set_bookmark(
            camera_id, datetime.utcnow(), ", ".join(labels),
        )

    async def trigger_recording(
        self, camera_id: str, duration_seconds: int = 30,
    ) -> CommandResult:
        if not self._client:
            return _unsupported("trigger_recording", camera_id, "Not connected")
        device_id = camera_id.removeprefix("nx:")
        try:
            # PATCH the device record to enable recording. Standard v4 surface.
            # v4 uses schedule.isEnabled rather than the v3 isRecording field.
            resp = await self._client.patch(
                f"/rest/v4/devices/{device_id}",
                json={"schedule": {"isEnabled": True}},
            )
            return _result(camera_id, "trigger_recording",
                           "accepted" if resp.status_code in (200, 204) else "rejected",
                           resp.text)
        except httpx.HTTPError as e:
            return _result(camera_id, "trigger_recording", "timeout", str(e))


# -- Helpers -------------------------------------------------------------

def _to_camera(d: dict) -> Camera:
    nx_status = d.get("status", "")
    cam_status = "online" if nx_status in ("Online", "Recording") else "offline"
    return Camera(
        camera_id=f"nx:{d.get('id', '')}",
        name=d.get("name", ""),
        vendor="nx_witness",
        status=cam_status,
        stream_url=d.get("url"),
        enabled=False,
        vendor_meta=d,
    )


def _result(camera_id: str, ctype: str, status: str, msg: str) -> CommandResult:
    return CommandResult(
        command_id=str(uuid.uuid4()), camera_id=camera_id,
        command_type=ctype, status=status, vendor_message=msg,
    )


def _unsupported(ctype: str, camera_id: str, msg: str) -> CommandResult:
    return _result(ctype=ctype, camera_id=camera_id, status="unsupported", msg=msg)
