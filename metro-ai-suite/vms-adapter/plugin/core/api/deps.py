# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""API dependencies for FastAPI dependency injection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from plugin.core.db.session import get_session_factory

if TYPE_CHECKING:
    from plugin.core.config import AppConfig
    from plugin.core.factory import VmsShimSet
    from plugin.base.interfaces import ICoreAppShim

# Module-level state set by the orchestrator at startup
_vms_shim_sets: list["VmsShimSet"] = []
_core_app_shims: dict[str, "ICoreAppShim"] = {}
_app_config: "AppConfig | None" = None


def set_shims(
    vms_shim_sets: list["VmsShimSet"],
    core_app_shims: "dict[str, ICoreAppShim] | ICoreAppShim | None",
    app_config: "AppConfig",
) -> None:
    """Called by the orchestrator at startup to inject shim instances."""
    global _vms_shim_sets, _core_app_shims, _app_config
    _vms_shim_sets = vms_shim_sets
    if core_app_shims is None:
        _core_app_shims = {}
    elif isinstance(core_app_shims, dict):
        _core_app_shims = core_app_shims
    else:
        _core_app_shims = {core_app_shims.app_id: core_app_shims}
    _app_config = app_config


async def get_db_session():
    """Yield an async database session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def get_vms_shim_sets():
    """Return the list of VMS shim sets."""
    return _vms_shim_sets


async def get_core_app_shims() -> "dict[str, ICoreAppShim]":
    """Return the full ``{app_id: shim}`` Core App registry."""
    return _core_app_shims


def require_core_app_shim(app_id: str) -> "ICoreAppShim":
    """Look up a Core App shim by id or raise 404."""
    shim = _core_app_shims.get(app_id)
    if shim is None:
        raise HTTPException(
            status_code=404,
            detail=f"Core app '{app_id}' is not registered",
        )
    return shim


async def get_app_config():
    """Return the application configuration."""
    return _app_config

