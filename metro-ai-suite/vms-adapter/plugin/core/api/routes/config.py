# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Configuration status endpoint."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from plugin.core.api.deps import get_app_config, get_nvr_shim_sets
from plugin.core.config import AppConfig
from plugin.core.factory import NvrShimSet
from plugin.core.models.domain import ConfigStatus

router = APIRouter()

_start_time = time.time()


@router.get("/config/status", response_model=ConfigStatus)
async def config_status(
    config: AppConfig = Depends(get_app_config),
    shim_sets: list[NvrShimSet] = Depends(get_nvr_shim_sets),
):
    """Loaded config + uptime + per-NVR connection status."""
    nvr_info = [
        {
            "name": s.name,
            "vendor": s.config.vendor,
            "mode": "rtsp",
            "connected": s.vms_shim.is_connected(),
        }
        for s in shim_sets
    ]
    core_apps_info = [
        {"type": ca.type, "base_url": ca.base_url}
        for ca in (config.core_apps if config else [])
    ]
    return ConfigStatus(
        uptime_seconds=round(time.time() - _start_time, 1),
        nvr_instances=nvr_info,
        core_apps=core_apps_info,
    )
