"""Core App discovery + generic start endpoint.

This router gives the UI a unified contract to:

* discover every Core App registered with the I/O plugin
  (``GET /v1/core-apps/discover``),
* fetch a Core App's parameter schema as JSON Schema
  (``GET /v1/core-apps/{app_id}/schema``),
* trigger an analytics run with validated parameters
  (``POST /v1/core-apps/{app_id}/start``).

Each Core App shim declares its own Pydantic ``param_model``; that model's
``model_json_schema()`` is what the UI uses to render inputs dynamically —
no front-end changes are required when a new Core App is added.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError

from plugin.core.api.deps import get_core_app_shims, require_core_app_shim
from plugin.base.interfaces import ICoreAppShim

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/core-apps", tags=["Core Apps"])


def _shim_descriptor(shim: ICoreAppShim, available: bool) -> dict[str, Any]:
    """Serialise a shim into the discovery payload."""
    return {
        "app_id": shim.app_id,
        "display_name": shim.display_name,
        "available": available,
        "params_schema": shim.param_model.model_json_schema(),
    }


@router.get("/discover")
async def discover_core_apps(
    shims: dict[str, ICoreAppShim] = Depends(get_core_app_shims),
) -> list[dict[str, Any]]:
    """List every registered Core App with its live availability + schema."""
    items: list[dict[str, Any]] = []
    for shim in shims.values():
        try:
            available = await shim.is_available()
        except Exception as exc:  # availability probes must never crash discovery
            logger.warning(
                "core_app_availability_check_failed",
                app_id=shim.app_id, error=str(exc),
            )
            available = False
        items.append(_shim_descriptor(shim, available))
    return items


@router.get("/{app_id}/schema")
async def get_core_app_schema(app_id: str) -> dict[str, Any]:
    """Return the JSON Schema for a Core App's start parameters."""
    shim = require_core_app_shim(app_id)
    return shim.param_model.model_json_schema()


@router.post("/{app_id}/start")
async def start_core_app(
    app_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Validate ``payload`` against the Core App's param model and start a run."""
    shim = require_core_app_shim(app_id)
    try:
        params = shim.param_model.model_validate(payload)
    except ValidationError as exc:
        # Re-raise as a 422 so FastAPI returns a structured field-error body.
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    try:
        result = await shim.start(params)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return result
