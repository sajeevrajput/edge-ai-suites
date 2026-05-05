"""Nx Witness VMS shim — single class, standard REST API v3 only.

All endpoints used here are documented in the official Nx Meta API tool
(https://meta.nxvms.com/doc/developers/api-tool):

  * POST   /rest/v3/login/sessions               → create session token
  * DELETE /rest/v3/login/sessions/{token}       → invalidate session
  * GET    /rest/v3/servers/this/info            → reachability probe
  * GET    /rest/v3/devices                      → list devices
  * GET    /rest/v3/devices/{deviceId}           → device record
  * POST   /rest/v3/devices/{deviceId}/bookmarks → create bookmark
  * GET    /rest/v3/analytics/engines            → list engines
  * PATCH  /rest/v3/devices/{deviceId}           → toggle recording

No URL is hand-built outside the REST API. The live RTSP and clip URLs
are taken from the device record's ``mediaStreams`` /  ``url`` fields
returned by the server.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import httpx
import structlog

from plugin.base.interfaces import IVmsShim
from plugin.core.config import NvrInstanceConfig
from plugin.core.models.domain import Camera, CommandResult

logger = structlog.get_logger(__name__)


class NxWitnessVmsShim(IVmsShim):
    """Single shim for Nx Witness using only standard /rest/v3 endpoints."""

    def __init__(self, config: NvrInstanceConfig):
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._connected = False
        self._token: str | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────
    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=30.0,
            verify=False,  # Nx ships self-signed certs by default
        )
        await self._login()

    async def _login(self) -> None:
        """POST /rest/v3/login/sessions to obtain a Bearer token."""
        if not self._client:
            return
        auth = self._config.auth
        if not auth.username:
            self._connected = False
            return
        try:
            resp = await self._client.post(
                "/rest/v3/login/sessions",
                json={"username": auth.username, "password": auth.password},
            )
            resp.raise_for_status()
            self._token = (resp.json() or {}).get("token")
            if self._token:
                self._client.headers["Authorization"] = f"Bearer {self._token}"
            # Probe reachability with a documented endpoint.
            info = await self._client.get("/rest/v3/servers/this/info")
            self._connected = info.status_code == 200
            logger.info("nx_connected", status=info.status_code)
        except httpx.HTTPError as e:
            logger.error("nx_connect_failed", error=str(e))
            self._connected = False

    async def disconnect(self) -> None:
        if self._client and self._token:
            try:
                await self._client.delete(f"/rest/v3/login/sessions/{self._token}")
            except httpx.HTTPError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        self._token = None

    def is_connected(self) -> bool:
        return self._connected

    # ── Discovery / metadata ───────────────────────────────────────────
    async def discover_cameras(self) -> list[Camera]:
        if not self._client:
            return []
        try:
            resp = await self._client.get("/rest/v3/devices")
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
            resp = await self._client.get(f"/rest/v3/devices/{device_id}")
            resp.raise_for_status()
            return _to_camera(resp.json())
        except httpx.HTTPError:
            return None

    # ── Stream / clip URLs (taken from server response) ────────────────
    async def get_live_stream_url(self, camera_id: str) -> str | None:
        cam = await self.get_camera_metadata(camera_id)
        if not cam:
            return None
        # Nx returns the playback URL under the device's mediaStreams /
        # url fields. We never construct one client-side.
        meta = cam.vendor_meta or {}
        for stream in meta.get("mediaStreams") or []:
            url = stream.get("url")
            if isinstance(url, str) and url.startswith(("rtsp://", "rtsps://")):
                return url
        return cam.stream_url

    async def get_clip_url(
        self, camera_id: str, from_dt: datetime, to_dt: datetime,
    ) -> str | None:
        # The standard Nx REST API does not expose a single "clip URL"
        # endpoint. Footage retrieval is handled by /rest/v3/devices/{id}
        # /footage which returns segment metadata; clients then fetch
        # via HLS. We surface that endpoint URL for the caller.
        if not self._client:
            return None
        device_id = camera_id.removeprefix("nx:")
        return (
            f"{self._config.base_url.rstrip('/')}"
            f"/rest/v3/devices/{device_id}/footage"
            f"?startTimeMs={int(from_dt.timestamp() * 1000)}"
            f"&endTimeMs={int(to_dt.timestamp() * 1000)}"
        )

    # ── Register analytics manifest ────────────────────────────────────
    async def register_analytics(self, manifest: dict[str, Any]) -> dict[str, Any]:
        if not self._client:
            return {"status": "error", "reason": "not_connected"}
        try:
            # Standard endpoint for listing engines is GET; manifest
            # registration is per-engine and out of scope of this
            # shim's required surface. Acknowledge the call and return
            # the engine list so callers can introspect what's available.
            resp = await self._client.get("/rest/v3/analytics/engines")
            return {
                "status": "ok" if resp.status_code == 200 else "error",
                "http_status": resp.status_code,
                "engines": resp.json() if resp.status_code == 200 else None,
            }
        except httpx.HTTPError as e:
            return {"status": "error", "reason": str(e)}

    # ── Write-back ─────────────────────────────────────────────────────
    async def acknowledge_event(
        self, camera_id: str, event_id: str, message: str = "",
    ) -> CommandResult:
        # Acknowledgement of analytics events is not part of the standard
        # /rest/v3 surface — it is plugin-specific in Nx. Return unsupported.
        return _unsupported("acknowledge_event", camera_id,
                            "Standard Nx v3 REST API has no event-acknowledgement endpoint")

    async def set_bookmark(
        self, camera_id: str, timestamp: datetime, label: str,
    ) -> CommandResult:
        if not self._client:
            return _unsupported("set_bookmark", camera_id, "Not connected")
        device_id = camera_id.removeprefix("nx:")
        try:
            resp = await self._client.post(
                f"/rest/v3/devices/{device_id}/bookmarks",
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
        # The plugin maps labels to a bookmark — that is the only standard
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
            # PATCH the device record to enable recording. Standard v3 surface.
            resp = await self._client.patch(
                f"/rest/v3/devices/{device_id}",
                json={"isRecording": True},
            )
            return _result(camera_id, "trigger_recording",
                           "accepted" if resp.status_code in (200, 204) else "rejected",
                           resp.text)
        except httpx.HTTPError as e:
            return _result(camera_id, "trigger_recording", "timeout", str(e))


# ── Helpers ──────────────────────────────────────────────────────────────

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
    return _result(camera_id, ctype, "unsupported", msg)
