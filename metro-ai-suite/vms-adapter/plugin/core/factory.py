"""Shim factory : registry-based factory for VMS and Core-App shims.

Vendors register themselves by name in ``_VMS_REGISTRY`` / ``_CORE_APP_REGISTRY``.
Adding a new VMS / Core-App is a one-line registration; no factory edits needed.
"""

from __future__ import annotations

from typing import Callable

import structlog

from plugin.base.interfaces import ICoreAppShim, IVmsShim
from plugin.core.config import AppConfig, AnyCorAppConfig, NvrInstanceConfig
from vms_shim.frigate.shim import FrigateVmsShim
from vms_shim.nxwitness.shim import NxWitnessVmsShim
from core_app_shim.lvc import LiveCaptioningCoreAppShim

logger = structlog.get_logger(__name__)


VmsShimBuilder = Callable[[NvrInstanceConfig], IVmsShim]
CoreAppShimBuilder = Callable[[AnyCorAppConfig], ICoreAppShim]

_VMS_REGISTRY: dict[str, VmsShimBuilder] = {
    "frigate": FrigateVmsShim,
    "nx_witness": NxWitnessVmsShim,
}

_CORE_APP_REGISTRY: dict[str, CoreAppShimBuilder] = {
    "live_captioning": LiveCaptioningCoreAppShim,
}


def register_vms(vendor: str, builder: VmsShimBuilder) -> None:
    """Register a new VMS vendor → shim constructor."""
    _VMS_REGISTRY[vendor] = builder


def register_core_app(app_type: str, builder: CoreAppShimBuilder) -> None:
    """Register a new Core App type → shim constructor."""
    _CORE_APP_REGISTRY[app_type] = builder


class NvrShimSet:
    """Holds the single ``IVmsShim`` for one configured NVR instance."""

    def __init__(self, name: str, config: NvrInstanceConfig, vms_shim: IVmsShim):
        self.name = name
        self.config = config
        self.vms_shim = vms_shim


class ShimFactory:
    @staticmethod
    def create_nvr_shims(config: AppConfig) -> list[NvrShimSet]:
        sets: list[NvrShimSet] = []
        for nvr in config.nvr_instances:
            builder = _VMS_REGISTRY.get(nvr.vendor)
            if builder is None:
                logger.warning("unknown_vendor", vendor=nvr.vendor, name=nvr.name)
                continue
            sets.append(NvrShimSet(name=nvr.name, config=nvr, vms_shim=builder(nvr)))
            logger.info("nvr_shim_created", name=nvr.name, vendor=nvr.vendor)
        return sets

    @staticmethod
    def create_core_app_shims(config: AppConfig) -> dict[str, ICoreAppShim]:
        registry: dict[str, ICoreAppShim] = {}
        for ca in config.core_apps:
            builder = _CORE_APP_REGISTRY.get(ca.type)
            if builder is None:
                logger.warning("unknown_core_app_type", type=ca.type)
                continue
            shim = builder(ca)
            if shim.app_id in registry:
                logger.warning("duplicate_core_app_id", app_id=shim.app_id)
                continue
            registry[shim.app_id] = shim
            logger.info("core_app_shim_created", app_id=shim.app_id)
        return registry

    @staticmethod
    def create_core_app_shim(config: AppConfig) -> ICoreAppShim | None:
        registry = ShimFactory.create_core_app_shims(config)
        return next(iter(registry.values()), None)

