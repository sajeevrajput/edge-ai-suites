# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Per-VMS endpoints : register analytics manifest with a specific VMS."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from plugin.core.api.deps import get_db_session, get_nvr_shim_sets
from plugin.core.db import repository as repo
from plugin.core.factory import NvrShimSet
from plugin.core.models.domain import NxAnalyticsIntegration, RegisterRequest

router = APIRouter()


@router.post("/vms/{name}/register")
async def register_vms(
    name: str,
    body: RegisterRequest,
    shim_sets: list[NvrShimSet] = Depends(get_nvr_shim_sets),
    db: AsyncSession = Depends(get_db_session),
):
    """Push an analytics manifest to one VMS.

    For Nx Witness:
      1. Returns cached approved credentials if already registered in DB.
      2. Loads manifests from request body, or falls back to config YAML.
      3. Calls the shim for Phase 1 REST registration.
      4. Persists credentials to DB so cameras can reuse the integration.

    For other vendors: delegates to shim's register_analytics() as before.
    """
    ss = next((s for s in shim_sets if s.name == name), None)
    if ss is None:
        raise HTTPException(status_code=404, detail=f"VMS '{name}' not configured")

    if ss.config.vendor != "nx_witness":
        return await ss.vms_shim.register_analytics(body.manifest)

    # --- Nx Witness path ---

    # Build manifests early (needed to derive manifest_id for Nx lookup)
    manifests = _build_manifests(body, ss)
    if manifests is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "No analytics manifests provided. Supply integrationManifest + engineManifest "
                "in the request body, or set analytics_manifest_path in config YAML."
            ),
        )

    manifest_id = manifests.get("integrationManifest", {}).get("id", body.core_app_id)

    # Check DB for existing record
    db_record = await repo.get_nx_integration(db, name, body.core_app_id)

    # Check Nx VMS for existing integration
    nx_record = await ss.vms_shim.find_integration_in_vms(manifest_id)

    # Decision tree: DB ✕ Nx
    if db_record and nx_record:
        # Both present — return DB record (source of truth)
        return db_record.model_dump(exclude={"id"})

    if not db_record and nx_record:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Integration '{manifest_id}' already exists in Nx VMS but has no DB record. "
                "Delete the integration from Nx and retry, or contact your administrator."
            ),
        )

    if db_record and not nx_record:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Integration '{manifest_id}' is recorded in the DB but is missing from Nx VMS. "
                "The integration may have been deleted from Nx manually. "
                "Delete the DB record and retry registration."
            ),
        )

    # Neither — create in Nx then save to DB
    result = await ss.vms_shim.register_analytics(manifests)

    # Persist to DB — normalize shim status to the DB-allowed set
    raw_status = result.get("status", "failed")
    _VALID_STATUSES = {"pending", "registered", "approved", "failed"}
    db_status = raw_status if raw_status in _VALID_STATUSES else "failed"

    integration: NxAnalyticsIntegration = await repo.upsert_nx_integration(
        db,
        vms_name=name,
        core_app_id=body.core_app_id,
        integration_manifest=manifests.get("integrationManifest", {}),
        engine_manifest=manifests.get("engineManifest", {}),
        device_agent_manifest=manifests.get("deviceAgentManifest"),
        nx_username=result.get("username"),
        nx_password=result.get("password"),
        nx_request_id=result.get("request_id"),
        status=db_status,
    )

    if raw_status == "error":
        raise HTTPException(
            status_code=502,
            detail=f"Nx integration registration failed: {result.get('reason', 'unknown')}",
        )

    # Inject credentials into the live shim so metadata push works immediately
    # without waiting for a service restart.
    username = result.get("username") or ""
    password = result.get("password") or ""
    if username and password:
        ss.vms_shim.set_integration_credentials(username, password)

    return integration.model_dump(exclude={"id"})


def _build_manifests(body: RegisterRequest, ss: NvrShimSet) -> dict | None:
    """Resolve manifests from request body or config file path."""
    # Prefer structured fields in the request body
    if body.integration_manifest and body.engine_manifest:
        manifests: dict = {
            "integrationManifest": body.integration_manifest,
            "engineManifest": body.engine_manifest,
            "pinCode": body.pin_code,
        }
        if body.device_agent_manifest:
            manifests["deviceAgentManifest"] = body.device_agent_manifest
        return manifests

    # Fall back to flat manifest dict with Nx-style keys
    if body.manifest and "integrationManifest" in body.manifest and "engineManifest" in body.manifest:
        return body.manifest

    # Fall back to config file path, or bundled default for nx_witness
    manifest_path = ss.config.analytics_manifest_path
    if not manifest_path:
        if ss.config.vendor == "nx_witness":
            from vms_shim.nxwitness.shim import DEFAULT_MANIFEST_PATH
            manifest_path = str(DEFAULT_MANIFEST_PATH)
        else:
            return None

    path = Path(manifest_path)
    if not path.exists():
        raise HTTPException(
            status_code=422,
            detail=f"analytics_manifest_path '{manifest_path}' not found",
        )

    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to parse manifest file '{manifest_path}': {exc}",
        ) from exc
