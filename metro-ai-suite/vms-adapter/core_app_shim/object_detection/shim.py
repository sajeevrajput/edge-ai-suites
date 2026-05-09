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

import json
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
        # Tracks active runs: run_id → {name, version, instance_id, camera_id}
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

        Fetches ``GET /pipelines``, extracts pipeline names, and builds a
        simple schema with ``pipeline_name``, ``camera_id``, and ``parameters``
        fields. Caches a dynamic Pydantic model for validation.
        """
        pipelines = await self._api.list_pipelines()
        pipeline_names = [
            p.get("name") or f"{p.get('type', 'unknown')}/{p.get('version', '1')}"
            for p in pipelines
            if isinstance(p, dict)
        ]

        schema: dict[str, Any] = {
            "type": "object",
            "title": f"{self.display_name} Start Parameters",
            "required": ["pipeline_name", "camera_id"],
            "properties": {
                "pipeline_name": {
                    "type": "string",
                    "title": "Pipeline",
                    "description": "Name of the pipeline template to run",
                    "enum": pipeline_names or [],
                    "x-vms-source": "pipeline",
                },
                "camera_id": {
                    "type": "string",
                    "title": "Camera",
                    "description": "Camera to process (RTSP URL resolved automatically)",
                    "x-vms-source": "camera-id",
                },
                "pipeline_version": {
                    "type": "string",
                    "title": "Pipeline version",
                    "default": "1",
                },
                "parameters": {
                    "type": "object",
                    "title": "Additional parameters",
                    "description": "Extra pipeline parameters passed as-is to the Pipeline Server",
                    "default": {},
                    "additionalProperties": True,
                },
            },
        }

        # Build a lightweight Pydantic model for validation
        self._param_model = create_model(
            "OdStartParams",
            pipeline_name=(str, ...),
            camera_id=(str, ...),
            pipeline_version=(str, "1"),
            parameters=(dict, {}),
        )

        return schema

    # ── ICoreAppShim — lifecycle ──────────────────────────────────────────────

    async def is_reachable(self) -> bool:
        return await self._api.is_reachable()

    async def start(self, params: BaseModel) -> dict[str, Any]:
        """Start a pipeline run for the given camera.

        The ``camera_id`` field is resolved to an RTSP URL by the generic
        run route before this method is called (see ``camera_fields()``).
        After resolution the field contains the RTSP URL string.
        """
        data = params.model_dump() if hasattr(params, "model_dump") else dict(params)

        pipeline_name: str = data.get("pipeline_name", "")
        pipeline_version: str = str(data.get("pipeline_version", "1"))
        # After RTSP resolution the camera_id field holds the stream URL
        stream_url: str = data.get("camera_id", "")
        extra_params: dict = data.get("parameters", {}) or {}

        if not pipeline_name:
            raise ValueError("pipeline_name is required")
        if not stream_url:
            raise ValueError("camera_id / stream URL is required")

        payload: dict[str, Any] = {
            "source": {"uri": stream_url, "type": "uri"},
            **extra_params,
        }

        result = await self._api.start_run(pipeline_name, pipeline_version, payload)
        if result is None:
            raise RuntimeError(
                f"Pipeline Server failed to start pipeline '{pipeline_name}/{pipeline_version}'"
            )

        instance_id = result.get("instance_id") or result.get("id") or str(result)
        run_id = f"{pipeline_name}/{pipeline_version}/{instance_id}"

        self._runs[run_id] = {
            "run_id": run_id,
            "pipeline_name": pipeline_name,
            "pipeline_version": pipeline_version,
            "instance_id": instance_id,
            "stream_url": stream_url,
        }

        logger.info(
            "od_run_started",
            run_id=run_id,
            pipeline=f"{pipeline_name}/{pipeline_version}",
        )
        return {
            "run_id": run_id,
            "pipeline_name": pipeline_name,
            "pipeline_version": pipeline_version,
            "instance_id": instance_id,
        }

    # ── ICoreAppShim — run management ─────────────────────────────────────────

    async def list_runs(self) -> list[dict[str, Any]]:
        return await self._api.list_runs()

    async def stop_run(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if run is None:
            # Try to parse run_id as "name/version/instance_id"
            parts = run_id.split("/", 2)
            if len(parts) == 3:  # noqa: PLR2004
                name, version, instance_id = parts
            else:
                logger.warning("od_stop_run_unknown_id", run_id=run_id)
                return False
        else:
            name = run["pipeline_name"]
            version = run["pipeline_version"]
            instance_id = run["instance_id"]

        ok = await self._api.stop_run(name, version, instance_id)
        if ok:
            self._runs.pop(run_id, None)
        return ok

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self._runs.get(run_id)
        if run is None:
            parts = run_id.split("/", 2)
            if len(parts) == 3:  # noqa: PLR2004
                name, version, instance_id = parts
            else:
                return None
        else:
            name = run["pipeline_name"]
            version = run["pipeline_version"]
            instance_id = run["instance_id"]
        return await self._api.get_run(name, version, instance_id)

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
