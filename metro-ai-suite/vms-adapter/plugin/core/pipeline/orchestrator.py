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


def _merge_label_types_into_manifest(
    manifests: dict,
    label_type_map: dict[str, str],
) -> None:
    """Merge typeIds from ``label_type_map`` into the Nx manifest dicts in-place.

    Adds any typeId that appears as a value in ``label_type_map`` (and is not
    already declared) to both ``engineManifest.typeLibrary.objectTypes`` and
    ``deviceAgentManifest.supportedTypes``.  This keeps the registered manifest
    in sync with whatever labels are configured without requiring manual JSON edits.
    """
    extra_type_ids = set(label_type_map.values())
    if not extra_type_ids:
        return

    # -- engineManifest.typeLibrary.objectTypes --
    engine = manifests.setdefault("engineManifest", {})
    type_library = engine.setdefault("typeLibrary", {})
    object_types: list[dict] = type_library.setdefault("objectTypes", [])
    existing_ids = {t.get("id") for t in object_types}
    for type_id in sorted(extra_type_ids):
        if type_id not in existing_ids:
            object_types.append({"id": type_id, "name": type_id})
            existing_ids.add(type_id)

    # -- deviceAgentManifest.supportedTypes --
    da_manifest = manifests.setdefault("deviceAgentManifest", {})
    supported: list[dict] = da_manifest.setdefault("supportedTypes", [])
    existing_supported = {t.get("objectTypeId") for t in supported}
    for type_id in sorted(extra_type_ids):
        if type_id not in existing_supported:
            supported.append({
                "objectTypeId": type_id,
                "attributes": ["boundingBox", "confidence"],
            })



class Orchestrator:
    def __init__(self, config: AppConfig):
        self.config = config
        self.nvr_shim_sets: list[NvrShimSet] = []
        self.core_app_shims: dict[str, ICoreAppShim] = {}
        self._shutdown_event = asyncio.Event()
        self._mqtt_tasks: list[asyncio.Task] = []

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
        await self._start_mqtt_subscribers()
        await self._start_lvc_mqtt_subscriber()

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

        # Derive core_app_id from the integration manifest id (e.g. "dlstreamer", "lvc")
        core_app_id = manifests.get("integrationManifest", {}).get("id", "default")
        manifest_id = core_app_id  # same value — manifest ID is used as username in Nx

        async with factory() as db:
            db_record = await repo.get_nx_integration(db, ss.name, core_app_id)

        nx_record = await ss.vms_shim.find_integration_in_vms(manifest_id)

        # Decision tree: DB ✕ Nx
        if db_record and nx_record:
            logger.info(
                "nx_integration_already_registered",
                nvr=ss.name,
                core_app_id=core_app_id,
                username=db_record.nx_username,
            )
            # Restore push credentials from DB so metadata push works after restart.
            if db_record.nx_username and db_record.nx_password:
                ss.vms_shim.set_integration_credentials(
                    db_record.nx_username, db_record.nx_password,
                )
                logger.info(
                    "nx_integration_credentials_restored",
                    nvr=ss.name,
                    username=db_record.nx_username,
                )
            else:
                logger.warning(
                    "nx_integration_no_password_in_db",
                    nvr=ss.name,
                    core_app_id=core_app_id,
                    detail="Metadata push unavailable — recreate the integration to store credentials.",
                )
            return

        if not db_record and nx_record:
            logger.error(
                "nx_integration_exists_in_vms_not_in_db",
                nvr=ss.name,
                core_app_id=core_app_id,
                detail=(
                    "The Nx VMS already has an integration with this manifest ID but the "
                    "VAP database has no record of it. Clean up the integration in Nx or "
                    "call POST /v1/vms/{name}/register to force re-registration."
                ),
            )
            return

        if db_record and not nx_record:
            logger.error(
                "nx_integration_exists_in_db_not_in_vms",
                nvr=ss.name,
                core_app_id=core_app_id,
                detail=(
                    "The VAP database has an integration record but it is missing from the "
                    "Nx VMS. The integration may have been deleted from Nx manually. "
                    "Delete the DB record and restart, or recreate the integration in Nx."
                ),
            )
            return

        # Merge any label_type_map typeIds from object_detection core apps into the manifest
        # so Nx recognises all configured types without manual manifest edits.
        from plugin.core.config import ObjectDetectionCoreAppConfig
        for ca_cfg in self.config.core_apps:
            if isinstance(ca_cfg, ObjectDetectionCoreAppConfig) and ca_cfg.label_type_map:
                _merge_label_types_into_manifest(manifests, ca_cfg.label_type_map)

        # Neither DB nor Nx has the integration — create fresh
        try:
            result = await ss.vms_shim.register_analytics(manifests)
        except Exception:
            logger.exception("nx_autoregister_failed", nvr=ss.name)
            return

        _VALID_STATUSES = {"pending", "registered", "approved", "failed"}
        nx_status = result.get("status", "failed")
        db_status = nx_status if nx_status in _VALID_STATUSES else "failed"
        async with factory() as db:
            await repo.upsert_nx_integration(
                db,
                vms_name=ss.name,
                core_app_id=core_app_id,
                integration_manifest=manifests.get("integrationManifest", {}),
                engine_manifest=manifests.get("engineManifest", {}),
                device_agent_manifest=manifests.get("deviceAgentManifest"),
                nx_username=result.get("username"),
                nx_password=result.get("password"),
                nx_request_id=result.get("request_id"),
                status=db_status,
            )

        logger.info(
            "nx_integration_autoregistered",
            nvr=ss.name,
            core_app_id=core_app_id,
            status=db_status,
            username=result.get("username"),
        )

        # Provide integration credentials to the shim so it can push metadata.
        password = result.get("password") or ""
        username = result.get("username") or ""
        if username and password:
            ss.vms_shim.set_integration_credentials(username, password)

    async def _start_lvc_mqtt_subscriber(self) -> None:
        """Start an LvcMqttSubscriber background task for each live_captioning shim."""
        from core_app_shim.lvc.mqtt_subscriber import LvcMqttSubscriber
        from plugin.core.config import LiveCaptioningCoreAppConfig

        for shim in self.core_app_shims.values():
            cfg = getattr(shim, "_config", None)
            if not isinstance(cfg, LiveCaptioningCoreAppConfig):
                continue
            if not self.config.mqtt.host:
                logger.info("lvc_mqtt_not_configured_skipping", app_id=shim.app_id)
                continue
            subscriber = LvcMqttSubscriber()
            shim.set_subscriber(subscriber)
            task = asyncio.create_task(
                subscriber.run(
                    mqtt_host=self.config.mqtt.host,
                    mqtt_port=self.config.mqtt.port,
                ),
                name=f"lvc-mqtt-subscriber-{shim.app_id}",
            )
            self._mqtt_tasks.append(task)
            logger.info(
                "lvc_mqtt_subscriber_task_started",
                app_id=shim.app_id,
                mqtt_host=self.config.mqtt.host,
                mqtt_port=self.config.mqtt.port,
            )

    async def _start_mqtt_subscribers(self) -> None:
        """Start an MqttSubscriber background task for each object_detection shim."""
        from core_app_shim.object_detection.mqtt_subscriber import MqttSubscriber
        from plugin.core.config import ObjectDetectionCoreAppConfig

        for shim in self.core_app_shims.values():
            cfg = getattr(shim, "_config", None)
            if not isinstance(cfg, ObjectDetectionCoreAppConfig):
                continue
            subscriber = MqttSubscriber()
            task = asyncio.create_task(
                subscriber.run(
                    mqtt_host=cfg.mqtt_host,
                    mqtt_port=cfg.mqtt_port,
                    nvr_shim_sets=self.nvr_shim_sets,
                    core_app_id=shim.app_id,
                    label_type_map=cfg.label_type_map,
                ),
                name=f"mqtt-subscriber-{shim.app_id}",
            )
            self._mqtt_tasks.append(task)
            logger.info(
                "mqtt_subscriber_task_started",
                app_id=shim.app_id,
                mqtt_host=cfg.mqtt_host,
                mqtt_port=cfg.mqtt_port,
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
        # Cancel all MQTT subscriber tasks (LVC + OD)
        for task in self._mqtt_tasks:
            task.cancel()
        if self._mqtt_tasks:
            await asyncio.gather(*self._mqtt_tasks, return_exceptions=True)
            logger.info("mqtt_subscribers_stopped", count=len(self._mqtt_tasks))
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
