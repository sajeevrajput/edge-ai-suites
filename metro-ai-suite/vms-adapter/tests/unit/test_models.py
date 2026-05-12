# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for Pydantic domain models."""

from datetime import datetime

from plugin.core.models.domain import (
    AnalysisResult,
    Camera,
    CameraEnableRequest,
    CommandResult,
    MetadataEvent,
)


def test_camera_defaults():
    cam = Camera(camera_id="frigate:front-door", name="Front Door", vendor="frigate")
    assert cam.status == "unknown"
    assert cam.enabled is False
    assert cam.vendor_meta == {}


def test_metadata_event():
    e = MetadataEvent(
        event_id="frigate:abc123",
        camera_id="frigate:front-door",
        event_type="recording_segment",
        started_at=datetime(2026, 3, 30, 12, 0, 0),
    )
    assert e.labels == []
    assert e.confidence is None


def test_analysis_result_with_labels():
    r = AnalysisResult(
        event_id="frigate:abc123", labels=["person", "car"],
        status="2 objects", bookmark=True,
    )
    assert len(r.labels) == 2
    assert r.bookmark is True
    assert r.trigger_recording is False


def test_command_result_unsupported():
    cr = CommandResult(
        command_id="cmd-1", camera_id="frigate:cam1",
        command_type="set_bookmark", status="unsupported",
    )
    assert cr.status == "unsupported"


def test_camera_enable_request():
    req = CameraEnableRequest(camera_ids=["frigate:cam1", "nx:cam2"], enabled=True)
    assert len(req.camera_ids) == 2
