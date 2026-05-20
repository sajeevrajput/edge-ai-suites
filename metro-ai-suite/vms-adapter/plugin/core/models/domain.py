# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v2 domain models : single source of truth for all data structures."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Camera(BaseModel):
    camera_id: str = Field(description="Vendor-prefixed stable ID, e.g. frigate:front-door")
    name: str
    vendor: str
    status: Literal["online", "offline", "unknown"] = "unknown"
    stream_url: str | None = None
    enabled: bool = False
    location: dict | None = None
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    vendor_meta: dict = Field(default_factory=dict)


class CameraView(BaseModel):
    camera_id: str
    name: str
    vendor: str
    status: Literal["online", "offline", "unknown"]
    enabled: bool
    stream_url: str | None = None

    @classmethod
    def from_camera(cls, cam: Camera) -> "CameraView":
        return cls(
            camera_id=cam.camera_id, name=cam.name, vendor=cam.vendor,
            status=cam.status, enabled=cam.enabled, stream_url=cam.stream_url,
        )


class MetadataEvent(BaseModel):
    """A video event reported by an Analytics App.

    Events originate in the App (which consumes RTSP from the plugin);
    the VMS is treated as a passive sink. The plugin records events so they
    can be routed back to the VMS via write-back commands.
    """
    event_id: str
    camera_id: str
    event_type: Literal[
        "motion", "object_detection", "recording_segment",
        "audio_event", "custom",
    ] | None = None
    started_at: datetime
    ended_at: datetime | None = None
    confidence: float | None = None
    labels: list[str] = Field(default_factory=list)
    clip_url: str | None = None
    vendor_meta: dict = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    event_id: str
    labels: list[str] = Field(default_factory=list)
    status: str | None = None
    disposition: Literal["acknowledged", "flagged", "dismissed"] | None = None
    bookmark: bool = False
    trigger_recording: bool = False
    vendor_meta: dict = Field(default_factory=dict)


class CommandResult(BaseModel):
    command_id: str
    camera_id: str
    command_type: Literal[
        "acknowledge_event", "set_bookmark", "push_label",
        "trigger_recording", "custom",
    ]
    status: Literal["accepted", "rejected", "timeout", "unsupported"]
    executed_at: datetime = Field(default_factory=datetime.utcnow)
    vendor_message: str | None = None


# ── API request/response models ─────────────────────────────────────────

class CameraEnableRequest(BaseModel):
    camera_ids: list[str]
    enabled: bool = True


class CameraEnableResponse(BaseModel):
    updated: list[str] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: bool
    vms_connected: bool


class ConfigStatus(BaseModel):
    uptime_seconds: float = 0.0
    vms_instances: list[dict] = Field(default_factory=list)
    analytics_apps: list[dict] = Field(default_factory=list)


class AnalyticsAppView(BaseModel):
    app_id: str
    display_name: str
    available: bool
    params_schema: dict


class StreamUrlResponse(BaseModel):
    """Response for GET /v1/cameras/{id}/live-stream."""
    camera_id: str
    rtsp_url: str


class ClipUrlResponse(BaseModel):
    """Response for GET /v1/cameras/{id}/clip."""
    camera_id: str
    clip_url: str
    from_dt: datetime
    to_dt: datetime


class RegisterRequest(BaseModel):
    """Generic request body for POST /v1/vms/{name}/register.

    Vendor-specific fields (e.g. Nx structured manifests) live in the
    corresponding shim's request model (e.g. NxRegisterRequest).
    """
    manifest: dict = Field(default_factory=dict)
    analytics_app_id: str = "default"


# ── Session models ───────────────────────────────────────────────────────────

class AnalyticsSessionCreate(BaseModel):
    """Input model for creating a new session (caller supplies these fields)."""
    camera_id: str
    analytics_app_id: str
    app_instance_id: str | None = None
    launch_payload: dict = Field(default_factory=dict)
    app_state: dict = Field(default_factory=dict)


class AnalyticsSession(BaseModel):
    """Full session record as stored in the database."""
    session_id: str
    camera_id: str
    analytics_app_id: str
    app_instance_id: str | None = None
    status: Literal["active", "stopped"]
    launch_payload: dict = Field(default_factory=dict)
    app_state: dict = Field(default_factory=dict)
    started_at: datetime
    stopped_at: datetime | None = None
