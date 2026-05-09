"""Abstract shim interfaces : single ``IVmsShim`` per VMS + optional ``ICoreAppShim``.

This module is the implementation of the chat-decision-overridden ADD
(see ``VMS_Plugin_ADD (2).docx`` — chat thread comments 1-3, 9, 26, 27,
32, 35-40, 52). Where the spec body and the chat thread differ, the
chat thread is authoritative. Concretely:

* **Single shim per VMS.** ``IVmsShim`` covers read + write + register;
  the original ``IVmsCommandShim`` is dropped (comments 1-3, 9).
* **Mode C only (RTSP).** No folder watchdog and no API polling are
  exposed by the interface. Apps consume RTSP directly via
  :meth:`get_live_stream_url` (comments 26, 27, 32).
* **Plugin facilitates auth — never stores it.** No ``connect``-time
  session keep-alive is required by the contract; auth is per-request
  for vendors that need it (comment 26).
* **App pulls; plugin does not push clips.** ``get_clip_url`` returns
  a URL — no file transfer (comments 35-37).
* **Per-shim register API.** :meth:`register_analytics` is the explicit
  hook the plugin calls on startup, and the ``POST /v1/vms/{name}/register``
  endpoint exposes it externally (comments 38-40, 52).
* **``ICoreAppShim`` is now optional.** It is retained only as a thin
  glue path for Core Apps (e.g. Live Video Captioning) that need a
  bespoke pipeline ``start()`` flow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from plugin.core.models.domain import AnalysisResult, Camera, CommandResult, MetadataEvent


class IVmsShim(ABC):
    """Single per-VMS abstraction : read + write + register."""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    async def discover_cameras(self) -> list[Camera]: ...

    @abstractmethod
    async def get_camera_metadata(self, camera_id: str) -> Camera | None: ...

    @abstractmethod
    async def get_live_stream_url(self, camera_id: str) -> str | None: ...

    @abstractmethod
    async def get_clip_url(
        self, camera_id: str, from_dt: datetime, to_dt: datetime,
    ) -> str | None: ...

    @abstractmethod
    async def register_analytics(self, manifest: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    async def acknowledge_event(
        self, camera_id: str, event_id: str, message: str = "",
    ) -> CommandResult: ...

    @abstractmethod
    async def set_bookmark(
        self, camera_id: str, timestamp: datetime, label: str,
    ) -> CommandResult: ...

    @abstractmethod
    async def push_label(
        self, camera_id: str, event_id: str, labels: list[str],
        confidence: float | None = None,
    ) -> CommandResult: ...

    @abstractmethod
    async def trigger_recording(
        self, camera_id: str, duration_seconds: int = 30,
    ) -> CommandResult: ...


class ICoreAppShim(ABC):
    """Optional thin App-Shim for Core Apps that need bespoke glue."""

    app_id: str = ""
    display_name: str = ""
    param_model: type[BaseModel] = BaseModel

    @abstractmethod
    async def fetch_schema(self) -> dict[str, Any]:
        """Fetch the JSON Schema for this app's start parameters from its own API.

        Implementations should:
        1. Call the core app's API (e.g. GET /openapi.json) to get the live schema.
        2. Build a dynamic Pydantic model via ``build_pydantic_from_schema()``.
        3. Cache the model so ``param_model`` returns it on subsequent calls.
        4. Return the raw JSON Schema dict (used as ``params_schema`` in discovery).

        If the app is unreachable, raise ``httpx.HTTPError`` or return the
        static fallback ``self.param_model.model_json_schema()``.
        """
        ...

    @abstractmethod
    async def deliver(
        self, event: MetadataEvent, clip_path: str,
    ) -> AnalysisResult | None: ...

    @abstractmethod
    async def is_reachable(self) -> bool: ...

    async def is_available(self) -> bool:
        return await self.is_reachable()

    @abstractmethod
    async def start(self, params: BaseModel) -> dict[str, Any]: ...

    # ── Generic run lifecycle (used by /v1/core-apps/{app_id}/runs routes) ────

    async def list_runs(self) -> list[dict[str, Any]]:
        """Return all active runs for this app. Override in concrete shims."""
        return []

    async def stop_run(self, run_id: str) -> bool:
        """Stop a run by ID. Return True on success. Override in concrete shims."""
        return False

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return details for a single run, or None if not found."""
        return None

    async def results_stream_url(self) -> str:
        """Return the SSE/HTTP URL the plugin should proxy for live results.

        The generic ``GET /results/stream`` route streams bytes from this URL.
        Raise ``NotImplementedError`` if the app does not support streaming.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support result streaming")

    async def get_options(self, option_type: str) -> list[Any]:
        """Return a list of options for a named dropdown (e.g. 'models', 'pipelines').

        The generic ``GET /options/{option_type}`` route calls this method.
        Return an empty list for unknown option types.
        """
        return []

    def camera_fields(self) -> list[str]:
        """Return field names whose values are camera IDs that need RTSP resolution.

        The generic start-run route resolves each listed field's value from a
        ``camera_id`` (as sent by the UI) to a ``stream_url`` before Pydantic
        validation, so the downstream shim always receives a real RTSP URL.

        Override this in concrete shims to declare camera-bound fields.
        The default returns an empty list (no camera resolution needed).
        """
        return []

    def mqtt_topic_prefix(self) -> str | None:
        """Return the MQTT topic prefix used by this Core App, or None if not using MQTT.

        The plugin's ``MqttResultClient`` subscribes to ``{prefix}/#`` at startup
        and routes messages to per-run queues consumed by the SSE result stream.

        Example: ``"live-video-captioning"`` → subscribes to
        ``"live-video-captioning/#"`` and processes topics like
        ``"live-video-captioning/{run_id}"``.

        Return ``None`` (default) if the app does not publish results via MQTT;
        the SSE route will fall back to proxying the app's HTTP SSE endpoint.
        """
        return None
