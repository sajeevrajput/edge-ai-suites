"""Object Detection Core App shim.

Integrates a DLStreamer Pipeline Server–based object detection application
(e.g. Pallet Defect Detection) as a VAP core app.

Data flow:
  Camera RTSP ──► DLStreamer Pipeline Server
                  └─► inference metadata via MQTT → MqttSubscriber
                                                      └─► Nx analytics push

Architecture
────────────
``ObjectDetectionCoreAppShim`` is composed of:

* :class:`~.api_client.ObjectDetectionApiClient`  — HTTP calls to the Pipeline Server
* :class:`~.mqtt_subscriber.MqttSubscriber`        — started externally by the orchestrator

This shim implements :class:`~plugin.base.interfaces.ICoreAppShim` so the
generic ``/v1/core-apps/{app_id}/…`` routes work without app-specific code.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, create_model

from plugin.base.interfaces import ICoreAppShim
from plugin.core.config import ObjectDetectionCoreAppConfig
from plugin.core.models.domain import AnalysisResult, MetadataEvent
from .api_client import ObjectDetectionApiClient

logger = structlog.get_logger(__name__)


class ObjectDetectionCoreAppShim(ICoreAppShim):
    """ICoreAppShim implementation for DLStreamer Pipeline Server–based apps."""

    def __init__(self, config: ObjectDetectionCoreAppConfig) -> None:
        self._config = config
        self._api = ObjectDetectionApiClient(base_url=config.base_url)
        self._param_model: type[BaseModel] = BaseModel
        # Maps pipeline version (user-facing name) → pipeline root (URL path segment)
        # e.g. "pallet_defect_detection" → "user_defined_pipelines"
        self._pipeline_root_map: dict[str, str] = {}
        # Tracks active runs: run_id (= instance_id hex) → run metadata
        self._runs: dict[str, dict[str, Any]] = {}

    @property
    def app_id(self) -> str:  # type: ignore[override]
        return self._config.app_id

    @property
    def display_name(self) -> str:  # type: ignore[override]
        return self._config.display_name

    @property
    def param_model(self) -> type[BaseModel]:
        return self._param_model

    # ── ICoreAppShim — schema ─────────────────────────────────────────────────

    async def fetch_schema(self) -> dict[str, Any]:
        """Build a JSON Schema from the available pipeline templates.

        Calls ``GET /pipelines``. Each entry has:
          - ``name``:    pipeline root directory (e.g. "user_defined_pipelines")
          - ``version``: user-facing pipeline identifier (e.g. "pallet_defect_detection")

        The UI ``pipeline_name`` field shows the ``version`` values.  The root
        is stored in ``_pipeline_root_map`` so ``start()`` can construct the
        correct POST URL: ``/pipelines/{root}/{version}``.
        """
        pipelines = await self._api.list_pipelines()

        self._pipeline_root_map = {}
        pipeline_names: list[str] = []
        for p in pipelines:
            if not isinstance(p, dict):
                continue
            root = p.get("name", "user_defined_pipelines")
            version = p.get("version", "")
            if version:
                self._pipeline_root_map[version] = root
                pipeline_names.append(version)

        schema: dict[str, Any] = {
            "type": "object",
            "title": f"{self.display_name} Start Parameters",
            "required": ["pipeline_name", "camera_id"],
            "properties": {
                "pipeline_name": {
                    "type": "string",
                    "title": "Pipeline",
                    "description": "Pipeline template to run",
                    "enum": pipeline_names or [],
                    "x-vms-source": "pipeline",
                },
                "camera_id": {
                    "type": "string",
                    "title": "Camera",
                    "description": "Camera to process (RTSP URL resolved automatically)",
                    "x-vms-source": "camera-id",
                },
                "parameters": {
                    "type": "object",
                    "title": "Pipeline parameters",
                    "description": (
                        "Extra parameters forwarded to the Pipeline Server payload. "
                        "E.g. {\"detection-properties\": {\"device\": \"CPU\"}}"
                    ),
                    "default": {},
                    "additionalProperties": True,
                    "x-format": "textarea",
                },
            },
        }

        self._param_model = create_model(
            "OdStartParams",
            pipeline_name=(str, ...),
            camera_id=(str, ...),
            camera_id_ref=(str, ""),   # original camera_id before RTSP resolution (e.g. "nx:abc123")
            parameters=(dict, {}),
        )

        return schema

    # ── ICoreAppShim — lifecycle ──────────────────────────────────────────────

    def _build_mqtt_topic(self, camera_id_ref: str) -> str:
        """Build the MQTT publish topic from the original camera_id.

        Format: ``{vendor_prefix}/{app_id}/{device_id}``
        Example: ``nx/pdd/e3e9a385-7fe0-3ba5-5482-a86cde7faf48``

        The subscriber listens on ``+/{app_id}/+`` and uses prefix-match on
        the first segment to find the right VMS shim (e.g. ``nx`` → ``nx-main``).
        """
        if ":" in camera_id_ref:
            vendor_prefix, device_id = camera_id_ref.split(":", 1)
        else:
            vendor_prefix, device_id = "vap", camera_id_ref or "unknown"
        return f"{vendor_prefix}/{self._config.app_id}/{device_id}"

    async def is_reachable(self) -> bool:
        return await self._api.is_reachable()

    async def start(self, params: BaseModel) -> dict[str, Any]:
        """Start a pipeline run for the given camera.

        The ``camera_id`` field is resolved to an RTSP URL by the generic
        run route before this method is called (see ``camera_fields()``).

        Payload sent to Pipeline Server::

            {
              "source": {"uri": "<rtsp_url>", "type": "uri"},
              "parameters": { ...extra_params from the ``parameters`` field }
            }
        """
        data = params.model_dump() if hasattr(params, "model_dump") else dict(params)

        pipeline_name: str = data.get("pipeline_name", "")
        stream_url: str = data.get("camera_id", "")
        extra_params: dict = data.get("parameters", {}) or {}
        camera_id_ref: str = data.get("camera_id_ref", "")

        if not pipeline_name:
            raise ValueError("pipeline_name is required")
        if not stream_url:
            raise ValueError("camera_id / stream URL is required")

        pipeline_root = self._pipeline_root_map.get(pipeline_name, "user_defined_pipelines")

        # Build MQTT topic from the original camera_id (e.g. "nx:abc123")
        # Topic format: "{vendor_prefix}/{app_id}/{device_id}" → matches subscriber filter "+/{app_id}+"
        # e.g. "nx/pdd/e3e9a385-7fe0-3ba5-5482-a86cde7faf48"
        mqtt_topic = self._build_mqtt_topic(camera_id_ref)

        payload: dict[str, Any] = {
            "source": {
                "uri": stream_url,
                "type": "uri",
                "properties": {
                    "protocols": "tcp",
                    "add-reference-timestamp-meta": True,
                    "latency": 100,
                },
            },
            "destination": {
                "metadata": {
                    "type": "mqtt",
                    "host": f"{self._config.pipeline_server_mqtt_host}:{self._config.pipeline_server_mqtt_port}",
                    "topic": mqtt_topic,
                },
            },
            "parameters": extra_params,
        }
        logger.info("-"*100)
        logger.info(payload)
        logger.info("-"*100)

        result = await self._api.start_run(pipeline_root, pipeline_name, payload)
        if result is None:
            raise RuntimeError(
                f"Pipeline Server failed to start pipeline '{pipeline_root}/{pipeline_name}'"
            )

        # instance_id is a hex UUID string returned by the Pipeline Server
        instance_id: str = str(result.get("instance_id") or result.get("id") or "")
        run_id = instance_id  # already URL-safe hex string

        self._runs[run_id] = {
            "run_id": run_id,
            "pipeline_name": pipeline_name,
            "pipeline_root": pipeline_root,
            "stream_url": stream_url,
        }

        logger.info("od_run_started", run_id=run_id, pipeline=f"{pipeline_root}/{pipeline_name}")
        return {
            "run_id": run_id,
            "pipeline_name": pipeline_name,
            "pipeline_root": pipeline_root,
        }

    # ── ICoreAppShim — run management ─────────────────────────────────────────

    async def list_runs(self) -> list[dict[str, Any]]:
        return await self._api.list_runs()

    async def stop_run(self, run_id: str) -> bool:
        """Stop a pipeline instance by its hex UUID run_id."""
        ok = await self._api.stop_run(run_id)
        if ok:
            self._runs.pop(run_id, None)
        return ok

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Get status of a pipeline instance by its hex UUID run_id."""
        return await self._api.get_run(run_id)

    # ── ICoreAppShim — deliver (not used — push model) ────────────────────────

    async def deliver(
        self, event: MetadataEvent, clip_path: str,
    ) -> AnalysisResult | None:
        """Not used: PDD uses MQTT push model, not event-triggered pull."""
        logger.debug("od_deliver_noop", event_id=event.event_id)
        return None

    def camera_fields(self) -> list[str]:
        """Return the field that holds camera IDs, triggering RTSP resolution."""
        return ["camera_id"]
