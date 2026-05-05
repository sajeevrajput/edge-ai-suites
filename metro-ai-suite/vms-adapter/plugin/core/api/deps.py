"""API dependencies for FastAPI dependency injection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from plugin.core.db.session import get_session_factory

if TYPE_CHECKING:
    from plugin.core.config import AppConfig
    from plugin.core.factory import NvrShimSet
    from plugin.base.interfaces import ICoreAppShim

# Module-level state set by the orchestrator at startup
_nvr_shim_sets: list["NvrShimSet"] = []
_core_app_shims: dict[str, "ICoreAppShim"] = {}
_app_config: "AppConfig | None" = None


def set_shims(
    nvr_shim_sets: list["NvrShimSet"],
    core_app_shims: "dict[str, ICoreAppShim] | ICoreAppShim | None",
    app_config: "AppConfig",
) -> None:
    """Called by the orchestrator at startup to inject shim instances.

    ``core_app_shims`` accepts either a ``{app_id: shim}`` registry (new
    multi-app behaviour) or a single shim (legacy callers).
    """
    global _nvr_shim_sets, _core_app_shims, _app_config
    _nvr_shim_sets = nvr_shim_sets
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


async def get_nvr_shim_sets():
    """Return the list of NVR shim sets."""
    return _nvr_shim_sets


async def get_core_app_shims() -> "dict[str, ICoreAppShim]":
    """Return the full ``{app_id: shim}`` Core App registry."""
    return _core_app_shims


async def get_core_app_shim() -> "ICoreAppShim | None":
    """Backward-compat: return the first registered Core App shim, if any."""
    return next(iter(_core_app_shims.values()), None)


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
