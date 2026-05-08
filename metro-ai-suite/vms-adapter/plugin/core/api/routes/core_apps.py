"""Core App discovery + generic run-lifecycle API.

This router gives the UI a unified contract to:

* discover every Core App registered with the I/O plugin
  ``GET  /v1/core-apps/discover``
* fetch a Core App's parameter schema as JSON Schema
  ``GET  /v1/core-apps/{app_id}/schema``
* start / stop / list pipeline runs — **generic, works for any app_id**
  ``POST   /v1/core-apps/{app_id}/runs``
  ``GET    /v1/core-apps/{app_id}/runs``
  ``GET    /v1/core-apps/{app_id}/runs/{run_id}``
  ``DELETE /v1/core-apps/{app_id}/runs/{run_id}``
* stream live results (captions, detections, …) from the core app
  ``GET  /v1/core-apps/{app_id}/results/stream``
* fetch dynamic dropdown options (models, pipelines, …)
  ``GET  /v1/core-apps/{app_id}/options/{option_type}``

Adding a **new core app** requires only a new shim class implementing
``ICoreAppShim`` — zero route changes needed here.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from sqlalchemy.ext.asyncio import AsyncSession

from plugin.core.api.deps import get_core_app_shims, get_db_session, require_core_app_shim
from plugin.core.db import repository as repo
from plugin.base.interfaces import ICoreAppShim

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/core-apps", tags=["Core Apps"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _shim_descriptor(
    shim: ICoreAppShim,
    available: bool,
    schema: dict | None,
    error: str | None = None,
) -> dict[str, Any]:
    """Serialise a shim into the discovery payload."""
    desc: dict[str, Any] = {
        "app_id": shim.app_id,
        "display_name": shim.display_name,
        "available": available,
        "params_schema": schema,
    }
    if error:
        desc["error"] = error
    return desc


def _require_shim(app_id: str) -> ICoreAppShim:
    shim = require_core_app_shim(app_id)
    if shim is None:
        raise HTTPException(status_code=404, detail=f"Core app '{app_id}' not registered")
    return shim


# ── Discovery & schema ────────────────────────────────────────────────────────

@router.get("/discover")
async def discover_core_apps(
    shims: dict[str, ICoreAppShim] = Depends(get_core_app_shims),
) -> list[dict[str, Any]]:
    """List every registered Core App with its live availability and schema.

    When a Core App is unreachable:
    - ``available`` is ``false``
    - ``params_schema`` is ``null``
    - ``error`` contains the reason (displayed in the UI)
    """
    items: list[dict[str, Any]] = []
    for shim in shims.values():
        error_msg: str | None = None

        try:
            available = await shim.is_available()
        except Exception as exc:
            logger.warning("core_app_availability_check_failed", app_id=shim.app_id, error=str(exc))
            available = False
            error_msg = str(exc)

        schema: dict | None = None
        if available:
            try:
                schema = await shim.fetch_schema()
            except Exception as exc:
                logger.warning("core_app_fetch_schema_failed", app_id=shim.app_id, error=str(exc))
                schema = None
                error_msg = str(exc)
        else:
            if not error_msg:
                error_msg = f"{shim.display_name} backend is not reachable"

        logger.info(
            "core_app_discovered",
            app_id=shim.app_id,
            available=available,
            has_schema=schema is not None,
        )
        items.append(_shim_descriptor(shim, available, schema, error_msg))
    return items


@router.get("/{app_id}/schema")
async def get_core_app_schema(app_id: str) -> dict[str, Any]:
    """Return the live JSON Schema for a Core App's start parameters.

    Returns 503 if the schema has not been loaded yet (call GET /discover first).
    """
    shim = _require_shim(app_id)
    try:
        return await shim.fetch_schema()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Run lifecycle — POST / GET / DELETE /{app_id}/runs ────────────────────────

@router.post("/{app_id}/runs")
async def start_core_app_run(
    app_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Validate ``payload`` against the app's live Pydantic schema and start a run.

    Pre-validation transformations applied (in order):
    1. **Camera resolution** — camera_id values (``x-vms-source: "camera-id"``) are
       resolved to RTSP stream_urls via the camera DB.
    2. **Frame resolution** — the ``frameResolution`` dropdown value is converted to
       ``frameWidth`` / ``frameHeight`` integers (matching LVC's ``frameQualitySelect``
       logic) and then stripped from the payload.
    3. **Synthetic field removal** — any remaining synthetic fields are removed so
       Pydantic only validates the real LVC API fields.

    Returns 503 if the schema has not been loaded yet (call GET /discover first).
    Returns 422 with per-field errors if payload fails validation.
    Returns 502 if the core app backend returns an error.
    """
    shim = _require_shim(app_id)

    try:
        model = shim.param_model
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    resolved_payload = dict(payload)

    # 1. Resolve camera-id values → RTSP stream_urls
    camera_field_set = set(shim.camera_fields())
    for field_name in camera_field_set:
        cam_value = resolved_payload.get(field_name)
        if cam_value and isinstance(cam_value, str):
            camera = await repo.get_camera(db, cam_value)
            if camera and camera.stream_url:
                resolved_payload[field_name] = camera.stream_url
                logger.info(
                    "core_app_camera_resolved",
                    field=field_name,
                    camera_id=cam_value,
                    stream_url=camera.stream_url,
                )
            else:
                raise HTTPException(
                    status_code=422,
                    detail=f"Camera '{cam_value}' not found or has no stream URL",
                )

    # 2. Expand frameResolution dropdown → frameWidth / frameHeight (matches LVC UI logic)
    _FRAME_PRESETS: dict[str, tuple[int, int]] = {
        "1280x720": (1280, 720),
        "640x480":  (640, 480),
        "480x360":  (480, 360),
    }
    frame_res = resolved_payload.pop("frameResolution", None)
    if frame_res and frame_res != "default":
        preset = _FRAME_PRESETS.get(frame_res)
        if preset:
            resolved_payload["frameWidth"]  = preset[0]
            resolved_payload["frameHeight"] = preset[1]

    # 3. Remove any remaining synthetic/ui-only keys unknown to the Pydantic model
    model_fields = set(model.model_fields.keys())
    for key in list(resolved_payload.keys()):
        if key not in model_fields:
            resolved_payload.pop(key)

    try:
        params = model.model_validate(resolved_payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    try:
        result = await shim.start(params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return result


@router.get("/{app_id}/runs")
async def list_core_app_runs(app_id: str) -> list[dict[str, Any]]:
    """List all active runs for a core app."""
    shim = _require_shim(app_id)
    return await shim.list_runs()


@router.get("/{app_id}/runs/{run_id}")
async def get_core_app_run(app_id: str, run_id: str) -> dict[str, Any]:
    """Get details of a single run."""
    shim = _require_shim(app_id)
    run = await shim.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return run


@router.delete("/{app_id}/runs/{run_id}", status_code=204, response_class=Response)
async def stop_core_app_run(app_id: str, run_id: str) -> Response:
    """Stop a running pipeline run."""
    shim = _require_shim(app_id)
    ok = await shim.stop_run(run_id)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Failed to stop run '{run_id}'")
    return Response(status_code=204)


# ── Results stream ────────────────────────────────────────────────────────────

@router.get("/{app_id}/results/stream")
async def stream_core_app_results(app_id: str) -> StreamingResponse:
    """Proxy the core app's live SSE result stream to the browser.

    The core app backend emits Server-Sent Events (captions, detections, …).
    This endpoint forwards that stream through the plugin so the UI never
    connects to the core app directly.

    Returns 501 if the app does not support streaming.
    """
    shim = _require_shim(app_id)
    try:
        sse_url = await shim.results_stream_url()
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    async def _proxy() -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream("GET", sse_url) as response:
                    async for chunk in response.aiter_bytes():
                        yield chunk
            except httpx.HTTPError as exc:
                logger.error("core_app_sse_proxy_error", app_id=app_id, error=str(exc))
                yield b'data: {"error": "stream disconnected"}\n\n'

    return StreamingResponse(
        _proxy(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Options (dynamic dropdowns) ───────────────────────────────────────────────

@router.get("/{app_id}/options/{option_type}")
async def get_core_app_options(app_id: str, option_type: str) -> list[Any]:
    """Return a list of options for a named dropdown (e.g. 'models', 'pipelines').

    Each core app shim implements ``get_options(option_type)`` and returns
    a list of strings or ``{label, value}`` objects.
    Returns an empty list for unknown option types.
    """
    shim = _require_shim(app_id)
    return await shim.get_options(option_type)

