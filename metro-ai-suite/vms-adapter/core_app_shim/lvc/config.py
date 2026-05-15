# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Configuration model for the Live Video Captioning core app shim."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class LiveCaptioningCoreAppConfig(BaseModel):
    type: Literal["live_captioning"] = "live_captioning"
    base_url: str
    mediamtx_url: str = ""
