"""Live Captioning proxy endpoints.

These routes proxy requests to the Live Video Captioning (LVC) FastAPI backend
and expose an SSE stream that the VMS-UI can subscribe to directly.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from plugin.core.api.deps import get_core_app_shims, get_db_session
from plugin.core.db import repository as repo
from core_app_sim.lvc.live_captioning import LiveCaptioningCoreAppShim

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/live-captioning")


def _get_lvc_shim(
    shims=Depends(get_core_app_shims),
) -> LiveCaptioningCoreAppShim:
    """Return the registered Live Captioning shim or 409 if not configured."""
    shim = shims.get("live_captioning")
    if not isinstance(shim, LiveCaptioningCoreAppShim):
        raise HTTPException(
            status_code=409,
            detail=(
                "Live Captioning core app is not configured. "
                "Add an entry with type: live_captioning under core_apps in config.yaml."
            ),
        )
    return shim


# ── Request / response models ─────────────────────────────────────────────────

class StartRunRequest(BaseModel):
    camera_id: str
    model: str = ""
    prompt: str = ""
    max_tokens: int = 100
    run_name: str = ""
    pipeline_name: str = ""
    frame_rate: int | None = None
    chunk_size: int | None = None
    frame_width: int | None = None
    frame_height: int | None = None
    caption_history: int | None = None
    detection_model_name: str | None = None
    detection_threshold: float | None = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/runs")
async def start_run(
    body: StartRunRequest,
    db: AsyncSession = Depends(get_db_session),
    shim: LiveCaptioningCoreAppShim = Depends(_get_lvc_shim),
):
    """Start a Live Captioning run for a camera.

    Resolves the camera's RTSP stream URL from the database, then calls the
    LVC backend to start a new pipeline run.
    """
    camera = await repo.get_camera(db, body.camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera '{body.camera_id}' not found")

    rtsp_url = camera.stream_url or camera.vendor_meta.get("stream_url") or camera.vendor_meta.get("rtspUrl")
    if not rtsp_url:
        raise HTTPException(
            status_code=422,
            detail=f"Camera '{body.camera_id}' has no stream_url configured",
        )

    run = await shim.start_run(
        rtsp_url=rtsp_url,
        model=body.model or shim._config.default_model,
        prompt=body.prompt or shim._config.default_prompt,
        max_tokens=body.max_tokens or shim._config.max_tokens,
        run_name=body.run_name or f"vms-{body.camera_id}",
        pipeline_name=body.pipeline_name or shim._config.default_pipeline,
        frame_rate=body.frame_rate,
        chunk_size=body.chunk_size,
        frame_width=body.frame_width,
        frame_height=body.frame_height,
        caption_history=body.caption_history,
        detection_model_name=body.detection_model_name,
        detection_threshold=body.detection_threshold,
    )
    if run is None:
        reachable = await shim.is_reachable()
        if not reachable:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"LVC backend is not reachable at {shim._config.base_url}. "
                    "Ensure the Live Video Captioning stack is running."
                ),
            )
        raise HTTPException(status_code=502, detail="LVC backend failed to start run")

    # Enrich with relative WebRTC URL (same-origin so the UI's nginx /whep
    # proxy can forward it to MediaMTX without CORS).
    peer_id = run.get("peerId", "")
    if peer_id:
        run["webrtcUrl"] = f"/whep/{peer_id}/whep"

    return run


@router.get("/runs")
async def list_runs(shim: LiveCaptioningCoreAppShim = Depends(_get_lvc_shim)):
    """List all active Live Captioning runs."""
    return await shim.get_runs()


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    shim: LiveCaptioningCoreAppShim = Depends(_get_lvc_shim),
):
    """Get details for a specific run."""
    run = await shim.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.delete("/runs/{run_id}", status_code=204, response_class=Response)
async def stop_run(
    run_id: str,
    shim: LiveCaptioningCoreAppShim = Depends(_get_lvc_shim),
):
    """Stop a Live Captioning pipeline run."""
    ok = await shim.stop_run(run_id)
    if not ok:
        raise HTTPException(status_code=502, detail="Failed to stop run on LVC backend")
    return Response(status_code=204)


@router.get("/pipelines", response_model=list[str])
async def list_pipelines(shim: LiveCaptioningCoreAppShim = Depends(_get_lvc_shim)):
    """List available LVC pipelines (e.g. GenAI_Pipeline_on_CPU, GenAI_Pipeline_on_GPU)."""
    raw = await shim.get_pipelines()
    out: list[str] = []
    for p in raw or []:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict):
            name = p.get("pipeline_name") or p.get("name")
            if name:
                out.append(name)
    return out


@router.get("/models", response_model=list[str])
async def list_models(shim: LiveCaptioningCoreAppShim = Depends(_get_lvc_shim)):
    """List available VLM models from the LVC backend."""
    raw = await shim.get_models()
    if isinstance(raw, dict):
        raw = raw.get("models", [])
    out: list[str] = []
    for m in raw or []:
        if isinstance(m, str):
            out.append(m)
        elif isinstance(m, dict):
            name = m.get("model_name") or m.get("name")
            if name:
                out.append(name)
    return out


@router.get("/stream")
async def metadata_stream(shim: LiveCaptioningCoreAppShim = Depends(_get_lvc_shim)):
    """Proxy the LVC SSE metadata stream to the browser.

    The LVC backend emits Server-Sent Events on /api/runs/metadata-stream.
    This endpoint forwards that stream so the VMS-UI can subscribe via a
    single origin without CORS issues.
    """
    lvc_sse_url = await shim.stream_url()

    async def _proxy() -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream("GET", lvc_sse_url) as response:
                    async for chunk in response.aiter_bytes():
                        yield chunk
            except httpx.HTTPError as exc:
                logger.error("lvc_sse_proxy_error", error=str(exc))
                yield b"data: {\"error\": \"LVC stream disconnected\"}\n\n"

    return StreamingResponse(
        _proxy(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
