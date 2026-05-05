"""Application orchestrator : startup, shutdown, dependency wiring.

RTSP-only model: connect each VMS shim, register analytics manifest,
inject deps. Apps consume RTSP via /v1/cameras/{id}/live-stream and POST
results back to /v1/analysis/results.
"""

from __future__ import annotations

import asyncio

import structlog

from plugin.base.interfaces import ICoreAppShim
from plugin.core.config import AppConfig, load_config
from plugin.core.db.session import close_db, init_db
from plugin.core.factory import NvrShimSet, ShimFactory

logger = structlog.get_logger(__name__)


_DEFAULT_MANIFEST: dict = {
    "engineId": "vms-adapter-plugin",
    "displayName": "VMS Adapter Plugin",
    "version": "1.0",
    "objectTypes": [{"id": "vms_plugin.detection", "name": "Detection"}],
    "eventTypes": [],
}


class Orchestrator:
    def __init__(self, config: AppConfig):
        self.config = config
        self.nvr_shim_sets: list[NvrShimSet] = []
        self.core_app_shims: dict[str, ICoreAppShim] = {}
        self._shutdown_event = asyncio.Event()

    @property
    def core_app_shim(self) -> ICoreAppShim | None:
        return next(iter(self.core_app_shims.values()), None)

    async def startup(self) -> None:
        logger.info("orchestrator_starting")

        await init_db(self.config.database.url)
        logger.info("database_initialized")

        self.nvr_shim_sets = ShimFactory.create_nvr_shims(self.config)
        self.core_app_shims = ShimFactory.create_core_app_shims(self.config)

        for ss in self.nvr_shim_sets:
            try:
                await ss.vms_shim.connect()
            except Exception:
                logger.exception("vms_connect_failed", nvr=ss.name)
                continue
            try:
                await ss.vms_shim.register_analytics(_DEFAULT_MANIFEST)
            except Exception:
                logger.exception("vms_register_failed", nvr=ss.name)

        self._wire_core_app_resolvers()

        from plugin.core.api.deps import set_shims
        set_shims(self.nvr_shim_sets, self.core_app_shims, self.config)

        logger.info("orchestrator_started", nvr_count=len(self.nvr_shim_sets))

    def _wire_core_app_resolvers(self) -> None:
        """Inject an RTSP resolver that defers to IVmsShim.get_live_stream_url."""

        async def resolve_rtsp(camera_id: str) -> str | None:
            for ss in self.nvr_shim_sets:
                prefix = "nx:" if ss.config.vendor == "nx_witness" else f"{ss.config.vendor}:"
                if camera_id.startswith(prefix):
                    try:
                        url = await ss.vms_shim.get_live_stream_url(camera_id)
                        if url:
                            return url
                    except Exception:
                        logger.exception("rtsp_resolve_failed", camera_id=camera_id)
            return None

        for shim in self.core_app_shims.values():
            if hasattr(shim, "set_rtsp_resolver"):
                shim.set_rtsp_resolver(resolve_rtsp)

    async def shutdown(self) -> None:
        logger.info("orchestrator_shutting_down")
        for ss in self.nvr_shim_sets:
            try:
                await ss.vms_shim.disconnect()
            except Exception:
                logger.exception("vms_disconnect_error", nvr=ss.name)
        await close_db()
        logger.info("orchestrator_stopped")


_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator | None:
    return _orchestrator


async def init_orchestrator(config_path: str | None = None) -> Orchestrator:
    global _orchestrator
    config = load_config(config_path)
    _orchestrator = Orchestrator(config)
    return _orchestrator
