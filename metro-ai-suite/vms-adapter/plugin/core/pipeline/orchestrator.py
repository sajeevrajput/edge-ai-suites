"""Application orchestrator : startup, shutdown, dependency wiring.

RTSP-only model: connect each VMS shim, register analytics manifest,
inject deps. Apps consume RTSP via /v1/cameras/{id}/live-stream and POST
results back to /v1/analysis/results.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

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
            if ss.config.vendor == "nx_witness" and ss.config.analytics_manifest_path:
                await self._autoregister_nx_integration(ss)
            else:
                try:
                    await ss.vms_shim.register_analytics(_DEFAULT_MANIFEST)
                except Exception:
                    logger.exception("vms_register_failed", nvr=ss.name)

        self._wire_core_app_resolvers()

        # Pre-fetch schemas so param_model is ready before the first /start call.
        for shim in self.core_app_shims.values():
            try:
                await shim.fetch_schema()
                logger.info("core_app_schema_fetched", app_id=shim.app_id)
            except Exception:
                logger.warning("core_app_schema_fetch_skipped", app_id=shim.app_id)

        from plugin.core.api.deps import set_shims
        set_shims(self.nvr_shim_sets, self.core_app_shims, self.config)

        await self._reconcile_sessions()

        logger.info("orchestrator_started", nvr_count=len(self.nvr_shim_sets))

    async def _autoregister_nx_integration(self, ss: NvrShimSet) -> None:
        """Register Nx analytics integration on startup if not already approved in DB."""
        from plugin.core.db.session import get_session_factory
        from plugin.core.db import repository as repo

        try:
            factory = get_session_factory()
        except RuntimeError:
            logger.warning("autoregister_skipped_no_db", nvr=ss.name)
            return

        async with factory() as db:
            existing = await repo.get_nx_integration(db, ss.name)
            if existing and existing.status == "approved":
                logger.info(
                    "nx_integration_already_registered",
                    nvr=ss.name,
                    username=existing.nx_username,
                )
                return

        manifest_path = Path(ss.config.analytics_manifest_path)  # type: ignore[arg-type]
        if not manifest_path.exists():
            logger.error(
                "nx_manifest_file_not_found",
                nvr=ss.name,
                path=str(manifest_path),
            )
            return

        try:
            with open(manifest_path) as f:
                manifests = json.load(f)
        except Exception as exc:
            logger.error(
                "nx_manifest_file_parse_failed",
                nvr=ss.name,
                path=str(manifest_path),
                error=str(exc),
            )
            return

        try:
            result = await ss.vms_shim.register_analytics(manifests)
        except Exception:
            logger.exception("nx_autoregister_failed", nvr=ss.name)
            return

        nx_status = result.get("status", "failed")
        async with factory() as db:
            await repo.upsert_nx_integration(
                db,
                vms_name=ss.name,
                integration_manifest=manifests.get("integrationManifest", {}),
                engine_manifest=manifests.get("engineManifest", {}),
                device_agent_manifest=manifests.get("deviceAgentManifest"),
                nx_username=result.get("username"),
                nx_password=result.get("password"),
                nx_request_id=result.get("request_id"),
                status=nx_status,
            )

        logger.info(
            "nx_integration_autoregistered",
            nvr=ss.name,
            status=nx_status,
            username=result.get("username"),
        )

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

    async def _reconcile_sessions(self) -> None:
        """On startup, verify active sessions are still alive on their apps.

        Sessions whose app instance no longer exists are marked stopped.
        """
        from plugin.core.db.session import get_session_factory
        from plugin.core.db import repository as repo

        try:
            factory = get_session_factory()
        except RuntimeError:
            logger.warning("reconcile_skipped_no_db")
            return

        async with factory() as db:
            active = await repo.list_sessions(db, status="active")

        if not active:
            return

        logger.info("reconciling_sessions", count=len(active))

        for s in active:
            shim = self.core_app_shims.get(s.core_app_id)
            alive = False
            if shim and s.app_instance_id:
                try:
                    # Use get_run if available (LVC); fall back to is_reachable.
                    if hasattr(shim, "get_run"):
                        result = await shim.get_run(s.app_instance_id)
                        alive = result is not None
                    else:
                        alive = await shim.is_reachable()
                except Exception:
                    logger.exception("reconcile_check_failed", session_id=s.session_id)

            if not alive:
                async with factory() as db:
                    await repo.stop_session(db, s.session_id)
                logger.info(
                    "session_reconciled_stopped",
                    session_id=s.session_id,
                    camera_id=s.camera_id,
                    core_app_id=s.core_app_id,
                )
            else:
                logger.info(
                    "session_reconciled_active",
                    session_id=s.session_id,
                    camera_id=s.camera_id,
                )

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
