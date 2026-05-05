"""Frigate VMS shim : single class, RTSP-only ingest model.

Uses ONLY the documented Frigate HTTP API
(https://docs.frigate.video/integrations/api/):

  * GET  /api/version                                   - reachability / version probe
  * GET  /api/config                                    - camera list + ffmpeg inputs (RTSP)
  * POST /api/events/<camera>/<label>/create            - manual event (trigger recording)
  * PUT  /api/events/<event_id>/end                     - end manual event
  * POST /api/events/<event_id>/sub_label               - push label / sub_label
  * POST /api/events/<event_id>/retain                  - retain (used as bookmark proxy)
  * GET  /api/<camera>/recordings/<start>,<end>/clip.mp4 - clip URL surfacing
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


class FrigateVmsShim(IVmsShim):
    """Single shim for Frigate (read + write + register)."""

    def __init__(self, config: NvrInstanceConfig):
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._connected = False

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(base_url=self._config.base_url, timeout=30.0)
        try:
            resp = await self._client.get("/api/version")
            resp.raise_for_status()
            self._connected = True
            logger.info("frigate_connected", version=resp.text.strip())
        except httpx.HTTPError as e:
            logger.error("frigate_connect_failed", error=str(e))
            self._connected = False

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def discover_cameras(self) -> list[Camera]:
        if not self._client:
            return []
        try:
            resp = await self._client.get("/api/config")
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            logger.error("frigate_discover_failed", error=str(e))
            return []

        cameras: list[Camera] = []
        for cam_name, cam_data in (data.get("cameras") or {}).items():
            enabled = cam_data.get("enabled", True)
            cameras.append(Camera(
                camera_id=f"frigate:{cam_name}",
                name=cam_name,
                vendor="frigate",
                status="online" if enabled else "offline",
                stream_url=self._extract_rtsp(cam_data),
                enabled=False,
                vendor_meta={"frigate_config": cam_data},
            ))
        logger.info("frigate_cameras_discovered", count=len(cameras))
        return cameras

    async def get_camera_metadata(self, camera_id: str) -> Camera | None:
        cams = await self.discover_cameras()
        return next((c for c in cams if c.camera_id == camera_id), None)

    @staticmethod
    def _extract_rtsp(cam_data: dict) -> str | None:
        for inp in (cam_data.get("ffmpeg") or {}).get("inputs") or []:
            path = (inp or {}).get("path")
            if isinstance(path, str) and path.startswith(("rtsp://", "rtsps://")):
                return path
        return None

    async def get_live_stream_url(self, camera_id: str) -> str | None:
        cam = await self.get_camera_metadata(camera_id)
        return cam.stream_url if cam else None

    async def get_clip_url(
        self, camera_id: str, from_dt: datetime, to_dt: datetime,
    ) -> str | None:
        if not self._config.base_url:
            return None
        cam_name = camera_id.removeprefix("frigate:")
        start = int(from_dt.timestamp())
        end = int(to_dt.timestamp())
        return (
            f"{self._config.base_url.rstrip('/')}"
            f"/api/{cam_name}/recordings/{start},{end}/clip.mp4"
        )

    async def register_analytics(self, manifest: dict[str, Any]) -> dict[str, Any]:
        # Frigate has no analytics-engine concept; uniform contract → no-op.
        logger.info("frigate_register_noop", keys=list(manifest.keys()))
        return {"status": "noop", "vendor": "frigate"}

    async def acknowledge_event(
        self, camera_id: str, event_id: str, message: str = "",
    ) -> CommandResult:
        # Per docs.frigate.video: PUT /api/events/<id>/end ends an in-progress
        # (typically manual) event. Closest semantic to "acknowledge".
        if not self._client:
            return _unsupported("acknowledge_event", camera_id, "Not connected")
        raw = event_id.removeprefix("frigate:")
        try:
            resp = await self._client.put(f"/api/events/{raw}/end")
            if resp.status_code == 200:
                return _result(camera_id, "acknowledge_event", "accepted", resp.text)
            return _unsupported("acknowledge_event", camera_id,
                                f"end_event status={resp.status_code}")
        except httpx.HTTPError as e:
            return _result(camera_id, "acknowledge_event", "timeout", str(e))

    async def set_bookmark(
        self, camera_id: str, timestamp: datetime, label: str,
    ) -> CommandResult:
        # Frigate has no first-class bookmark concept; the closest documented
        # primitive is event-retention. We surface this clearly as unsupported.
        return _unsupported("set_bookmark", camera_id, "Frigate has no bookmark API")

    async def push_label(
        self, camera_id: str, event_id: str, labels: list[str],
        confidence: float | None = None,
    ) -> CommandResult:
        if not self._client:
            return _unsupported("push_label", camera_id, "Not connected")
        raw = event_id.removeprefix("frigate:")
        try:
            resp = await self._client.post(
                f"/api/events/{raw}/sub_label",
                json={"subLabel": ", ".join(labels)},
            )
            return _result(camera_id, "push_label",
                           "accepted" if resp.status_code == 200 else "rejected",
                           resp.text)
        except httpx.HTTPError as e:
            return _result(camera_id, "push_label", "timeout", str(e))

    async def trigger_recording(
        self, camera_id: str, duration_seconds: int = 30,
    ) -> CommandResult:
        # Per docs.frigate.video: POST /api/events/<camera>/<label>/create
        # creates a manual event with auto-end after `duration` seconds and
        # `include_recording: true` ensures it's persisted to recordings.
        if not self._client:
            return _unsupported("trigger_recording", camera_id, "Not connected")
        cam_name = camera_id.removeprefix("frigate:")
        try:
            resp = await self._client.post(
                f"/api/events/{cam_name}/manual/create",
                json={
                    "duration": duration_seconds,
                    "include_recording": True,
                    "source_type": "api",
                },
            )
            return _result(camera_id, "trigger_recording",
                           "accepted" if resp.status_code == 200 else "rejected",
                           resp.text)
        except httpx.HTTPError as e:
            return _result(camera_id, "trigger_recording", "timeout", str(e))


def _result(camera_id: str, ctype: str, status: str, msg: str) -> CommandResult:
    return CommandResult(
        command_id=str(uuid.uuid4()), camera_id=camera_id,
        command_type=ctype, status=status, vendor_message=msg,
    )


def _unsupported(ctype: str, camera_id: str, msg: str) -> CommandResult:
    return _result(camera_id, ctype, "unsupported", msg)
