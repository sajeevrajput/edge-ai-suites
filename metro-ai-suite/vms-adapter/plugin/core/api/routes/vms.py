"""Per-VMS endpoints : register analytics manifest with a specific VMS."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from plugin.core.api.deps import get_nvr_shim_sets
from plugin.core.factory import NvrShimSet
from plugin.core.models.domain import RegisterRequest

router = APIRouter()


@router.post("/vms/{name}/register")
async def register_vms(
    name: str,
    body: RegisterRequest,
    shim_sets: list[NvrShimSet] = Depends(get_nvr_shim_sets),
):
    """Push an analytics manifest to one VMS (no-op for vendors w/o engines)."""
    ss = next((s for s in shim_sets if s.name == name), None)
    if ss is None:
        raise HTTPException(status_code=404, detail=f"VMS '{name}' not configured")
    return await ss.vms_shim.register_analytics(body.manifest)
