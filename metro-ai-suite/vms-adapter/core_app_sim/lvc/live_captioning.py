"""Live Video Captioning Core App shim.

Integrates the Intel Live Video Captioning application as the VMS core app.

Data flow:
  Camera RTSP ──► LVC stack (DLStreamer + gvagenai VLM)
                  ├─► captions via MQTT → SSE  → dashboard
                  └─► annotated video  → MediaMTX WebRTC → dashboard

This shim calls the LVC FastAPI backend to start / stop pipeline runs and
exposes helpers used by the proxy routes layer.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field

from plugin.core.config import LiveCaptioningCoreAppConfig
from plugin.core.models.domain import AnalysisResult, MetadataEvent
from plugin.base.interfaces import ICoreAppShim

logger = structlog.get_logger(__name__)


class LiveCaptioningStartParams(BaseModel):
    """Parameters accepted by :meth:`LiveCaptioningCoreAppShim.start`.

    Mirrors the default-visible inputs of the Live Video Captioning
    application's own UI (``live-video-captioning/app/ui/index.html``)
    so the VMS-rendered form only asks for what LVC actually exposes.

    The JSON why  of this model is what the VMS-UI consumes via
    ``GET /v1/core-apps/live_captioning/schema`` to render its inputs.
    """

    cameraId: str = Field(
        ...,
        title="Camera",
        description="Camera whose RTSP stream to caption (resolved to the LVC RTSP URL).",
        json_schema_extra={"x-vms-source": "camera"},
    )
    prompt: str = Field(
        default="",
        title="Prompt",
        description="Prompt sent to the VLM.",
    )
    model: str = Field(
        default="",
        title="VLM Model",
        description="Vision-language model name (empty = backend default).",
        json_schema_extra={"x-vms-source": "lvc-models"},
    )
    maxTokens: int = Field(
        default=70, ge=1, le=4096,
        title="Max Tokens",
        description="Maximum number of tokens generated per caption.",
    )
    frameRate: int = Field(
        default=1, ge=0,
        title="Frame Rate",
        description="Frames per second to feed the pipeline.",
    )
    chunkSize: int = Field(
        default=1, ge=1,
        title="Chunk Size",
        description="Number of frames per VLM chunk.",
    )
    captionHistory: int = Field(
        default=3, ge=0,
        title="Caption History",
        description="Number of previous captions to keep as context.",
    )
    pipelineName: str = Field(
        default="",
        title="Pipeline",
        description="LVC pipeline name (empty = configured default).",
        json_schema_extra={"x-vms-source": "lvc-pipelines"},
    )
    runName: str = Field(
        default="",
        title="Run Name",
        description="Human-friendly name for this run.",
    )


class LiveCaptioningCoreAppShim(ICoreAppShim):
    """ICoreAppShim implementation for the Live Video Captioning app."""

    app_id = "live_captioning"
    display_name = "Live Video Captioning"
    param_model = LiveCaptioningStartParams

    def __init__(self, config: LiveCaptioningCoreAppConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        # Resolver injected by the API layer to map cameraId → RTSP URL.
        # Signature: ``async def(camera_id: str) -> str | None``.
        self._rtsp_resolver = None
        self._last_start_error: str | None = None

    def set_rtsp_resolver(self, resolver) -> None:
        """Inject a coroutine that resolves a cameraId to an RTSP URL."""
        self._rtsp_resolver = resolver

    # ── internal ──────────────────────────────────────────────────────────────

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._config.delivery_timeout_seconds,
            )
        return self._client

    # ── ICoreAppShim ──────────────────────────────────────────────────────────

    async def deliver(
        self,
        event: MetadataEvent,
        clip_path: str,
    ) -> AnalysisResult | None:
        """Start a Live Captioning run for the camera's RTSP .

        `clip_path` is ignored — LVC works on live RTSP, not recorded clips.
        The camera's `stream_url` (stored in vendor_meta or the domain Camera
        model) is used as the RTSP source.
        """
        rtsp_url = event.vendor_meta.get("stream_url") or event.vendor_meta.get("rtsp_url")
        if not rtsp_url:
            logger.warning(
                "lvc_no_rtsp_url",
                event_id=event.event_id,
                camera_id=event.camera_id,
                hint="Set stream_url in event.vendor_meta or camera.stream_url",
            )
            return None

        run = await self.start_run(
            rtsp_url=rtsp_url,
            model=self._config.default_model,
            prompt=self._config.default_prompt,
            max_tokens=self._config.max_tokens,
            run_name=f"vms-{event.camera_id}",
        )
        if not run:
            return None

        run_id = run.get("runId", "")
        peer_id = run.get("peerId", "")
        mqtt_topic = run.get("mqttTopic", "")

        # Build relative WebRTC WHEP URL (same-origin → UI nginx /whep proxy)
        webrtc_url = f"/whep/{peer_id}/whep" if peer_id else ""

        logger.info(
            "lvc_run_started",
            run_id=run_id,
            peer_id=peer_id,
            webrtc_url=webrtc_url,
        )

        return AnalysisResult(
            event_id=event.event_id,
            labels=["live_captioning"],
            status="running",
            vendor_meta={
                "run_id": run_id,
                "peer_id": peer_id,
                "webrtc_url": webrtc_url,
                "mqtt_topic": mqtt_topic,
                "rtsp_url": rtsp_url,
            },
        )

    async def is_reachable(self) -> bool:
        """Check LVC backend reachability via GET /api/runs."""
        client = self._ensure_client()
        try:
            resp = await client.get("/api/runs")
            return resp.status_code < 500
        except httpx.HTTPError:
            return False

    # ── Extra helpers (used by proxy routes) ──────────────────────────────────

    async def start_run(
        self,
        rtsp_url: str,
        model: str = "",
        prompt: str = "",
        max_tokens: int = 100,
        run_name: str = "",
        pipeline_name: str = "",
        frame_rate: int | None = None,
        chunk_size: int | None = None,
        frame_width: int | None = None,
        frame_height: int | None = None,
        caption_history: int | None = None,
        detection_model_name: str | None = None,
        detection_threshold: float | None = None,
    ) -> dict | None:
        """POST /api/runs — start a new captioning pipeline."""
        client = self._ensure_client()
        payload: dict = {
            "rtspUrl": rtsp_url,
            "pipelineName": pipeline_name or self._config.default_pipeline,
        }
        if model:              payload["modelName"]     = model
        if prompt:             payload["prompt"]        = prompt
        if max_tokens:         payload["maxNewTokens"]  = max_tokens
        if run_name:           payload["runName"]       = run_name
        if frame_rate is not None:          payload["frameRate"]          = frame_rate
        if chunk_size is not None:          payload["chunkSize"]          = chunk_size
        if frame_width is not None:         payload["frameWidth"]         = frame_width
        if frame_height is not None:        payload["frameHeight"]        = frame_height
        if detection_model_name is not None: payload["detectionModelName"] = detection_model_name
        if detection_threshold is not None:  payload["detectionThreshold"] = detection_threshold

        try:
            resp = await client.post("/api/runs", json=payload)
            if not resp.is_success:
                logger.error(
                    "lvc_start_run_failed",
                    status_code=resp.status_code,
                    response_body=resp.text,
                    rtsp_url=rtsp_url,
                )
                self._last_start_error = self._extract_lvc_error(resp)
                return None
            self._last_start_error = None
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error("lvc_start_run_failed", error=str(exc))
            self._last_start_error = str(exc)
            return None

    @staticmethod
    def _extract_lvc_error(resp: httpx.Response) -> str:
        """Pull a human-readable message out of an LVC error response."""
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            return resp.text or f"HTTP {resp.status_code}"
        detail = data.get("detail", data) if isinstance(data, dict) else data
        if isinstance(detail, dict):
            body = detail.get("body") or detail.get("message") or ""
            if isinstance(body, str):
                body = body.strip().strip('"')
            return body or detail.get("message") or str(detail)
        return str(detail)

    async def get_runs(self) -> list[dict]:
        """GET /api/runs — list all active runs."""
        client = self._ensure_client()
        try:
            resp = await client.get("/api/runs")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error("lvc_get_runs_failed", error=str(exc))
            return []

    async def get_run(self, run_id: str) -> dict | None:
        """GET /api/runs/{run_id}."""
        client = self._ensure_client()
        try:
            resp = await client.get(f"/api/runs/{run_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error("lvc_get_run_failed", run_id=run_id, error=str(exc))
            return None

    async def stop_run(self, run_id: str) -> bool:
        """DELETE /api/runs/{run_id} — stop a pipeline run."""
        client = self._ensure_client()
        try:
            resp = await client.delete(f"/api/runs/{run_id}")
            resp.raise_for_status()
            logger.info("lvc_run_stopped", run_id=run_id)
            return True
        except httpx.HTTPError as exc:
            logger.error("lvc_stop_run_failed", run_id=run_id, error=str(exc))
            return False

    async def get_pipelines(self) -> list[dict]:
        """GET /api/pipelines — list available LVC pipelines.

        Retries once on transient 502 (LVC pipeline server warm-up).
        """
        client = self._ensure_client()
        for attempt in range(2):
            try:
                resp = await client.get("/api/pipelines")
                resp.raise_for_status()
                data = resp.json()
                return data.get("pipelines", data) if isinstance(data, dict) else data
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 502 and attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                logger.error("lvc_get_pipelines_failed", error=str(exc))
                return []
            except httpx.HTTPError as exc:
                logger.error("lvc_get_pipelines_failed", error=str(exc))
                return []
        return []

    async def get_models(self) -> list[dict]:
        """GET /api/vlm-models — list available VLM models."""
        client = self._ensure_client()
        try:
            resp = await client.get("/api/vlm-models")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error("lvc_get_models_failed", error=str(exc))
            return []

    async def stream_url(self) -> str:
        """Return the SSE metadata stream URL on the LVC backend."""
        base = self._config.base_url.rstrip("/")
        return f"{base}/api/runs/metadata-stream"

    # ── Generic discovery / start API ────────────────────────────────────────

    async def start(self, params) -> dict[str, Any]:
        """Validate-friendly entry point used by ``POST /v1/core-apps/{id}/start``.

        Resolves ``cameraId`` to an RTSP URL using the injected resolver,
        starts a pipeline run on the LVC backend, and enriches the response
        with a WebRTC WHEP URL so the UI can embed the live stream.
        """
        if not isinstance(params, LiveCaptioningStartParams):
            params = LiveCaptioningStartParams.model_validate(params)

        if self._rtsp_resolver is None:
            raise RuntimeError(
                "LiveCaptioningCoreAppShim: no rtsp_resolver injected; "
                "the API layer must call set_rtsp_resolver() at startup.",
            )
        rtsp_url = await self._rtsp_resolver(params.cameraId)
        if not rtsp_url:
            raise ValueError(
                f"Camera '{params.cameraId}' has no stream_url configured",
            )

        # Resolve pipeline: prefer user input, then config default, then
        # auto-fallback to the first pipeline the LVC server actually exposes.
        pipeline_name = params.pipelineName or self._config.default_pipeline
        try:
            available = await self.get_pipelines()
            available_names = [
                (p.get("pipeline_name") if isinstance(p, dict) else str(p))
                for p in (available or [])
            ]
            available_names = [n for n in available_names if n]
            if pipeline_name not in available_names and available_names:
                logger.warning(
                    "lvc_pipeline_fallback",
                    requested=pipeline_name,
                    available=available_names,
                    using=available_names[0],
                )
                pipeline_name = available_names[0]
        except Exception:  # noqa: BLE001 — fall through to start_run, which surfaces errors
            available_names = []

        run = await self.start_run(
            rtsp_url=rtsp_url,
            model=params.model or self._config.default_model,
            prompt=params.prompt or self._config.default_prompt,
            max_tokens=params.maxTokens or self._config.max_tokens,
            run_name=params.runName or f"vms-{params.cameraId}",
            pipeline_name=pipeline_name,
            frame_rate=params.frameRate,
            chunk_size=params.chunkSize,
            caption_history=params.captionHistory,
        )
        if run is None:
            reachable = await self.is_reachable()
            if not reachable:
                raise RuntimeError("LVC backend not reachable")
            if available_names and pipeline_name not in available_names:
                raise RuntimeError(
                    f"LVC pipeline '{pipeline_name}' not found. "
                    f"Available pipelines: {', '.join(available_names)}",
                )
            detail = self._last_start_error or "LVC backend failed to start run"
            raise RuntimeError(f"LVC backend failed to start run: {detail}")

        # Enrich with relative WebRTC URL (same-origin → UI nginx /whep proxy).
        peer_id = run.get("peerId", "")
        if peer_id:
            run["webrtcUrl"] = f"/whep/{peer_id}/whep"

        return run
