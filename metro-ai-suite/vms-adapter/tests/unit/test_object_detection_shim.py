"""Unit tests for ObjectDetectionCoreAppShim."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core_app_shim.object_detection.shim import ObjectDetectionCoreAppShim
from plugin.core.config import ObjectDetectionCoreAppConfig


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_config(**kwargs) -> ObjectDetectionCoreAppConfig:
    defaults = {
        "type": "object_detection",
        "app_id": "pdd",
        "display_name": "Pallet Defect Detection",
        "base_url": "http://localhost:8080",
        "mqtt_host": "localhost",
        "mqtt_port": 1883,
    }
    defaults.update(kwargs)
    return ObjectDetectionCoreAppConfig(**defaults)


def _make_shim(**kwargs) -> ObjectDetectionCoreAppShim:
    return ObjectDetectionCoreAppShim(_make_config(**kwargs))


# ── Basic identity ─────────────────────────────────────────────────────────────

def test_shim_app_id():
    shim = _make_shim()
    assert shim.app_id == "pdd"


def test_shim_display_name():
    shim = _make_shim(display_name="My PDD App")
    assert shim.display_name == "My PDD App"


def test_shim_implements_interface():
    from plugin.base.interfaces import ICoreAppShim
    shim = _make_shim()
    assert isinstance(shim, ICoreAppShim)


def test_camera_fields_returns_camera_id():
    shim = _make_shim()
    assert shim.camera_fields() == ["camera_id"]


# ── fetch_schema ──────────────────────────────────────────────────────────────

async def test_fetch_schema_returns_object_schema():
    shim = _make_shim()
    mock_pipelines = [
        {"name": "object_detection", "version": "1"},
        {"name": "defect_detection", "version": "2"},
    ]
    shim._api.list_pipelines = AsyncMock(return_value=mock_pipelines)

    schema = await shim.fetch_schema()

    assert schema["type"] == "object"
    assert "pipeline_name" in schema["properties"]
    assert "camera_id" in schema["properties"]
    assert set(schema["required"]) == {"pipeline_name", "camera_id"}


async def test_fetch_schema_populates_pipeline_enum():
    shim = _make_shim()
    shim._api.list_pipelines = AsyncMock(
        return_value=[{"name": "pipe_A"}, {"name": "pipe_B"}]
    )
    schema = await shim.fetch_schema()
    enum = schema["properties"]["pipeline_name"]["enum"]
    assert "pipe_A" in enum
    assert "pipe_B" in enum


async def test_fetch_schema_handles_empty_pipeline_list():
    shim = _make_shim()
    shim._api.list_pipelines = AsyncMock(return_value=[])
    schema = await shim.fetch_schema()
    assert schema["properties"]["pipeline_name"]["enum"] == []


# ── is_reachable ──────────────────────────────────────────────────────────────

async def test_is_reachable_delegates_to_api_client():
    shim = _make_shim()
    shim._api.is_reachable = AsyncMock(return_value=True)
    assert await shim.is_reachable() is True

    shim._api.is_reachable = AsyncMock(return_value=False)
    assert await shim.is_reachable() is False


# ── start ─────────────────────────────────────────────────────────────────────

async def test_start_creates_run():
    shim = _make_shim()
    shim._api.start_run = AsyncMock(
        return_value={"instance_id": 42, "pipeline": "object_detection/1"}
    )

    params = MagicMock()
    params.model_dump.return_value = {
        "pipeline_name": "object_detection",
        "pipeline_version": "1",
        "camera_id": "rtsp://cam:554/stream",
        "parameters": {},
    }

    result = await shim.start(params)

    shim._api.start_run.assert_called_once_with(
        "object_detection",
        "1",
        {"source": {"uri": "rtsp://cam:554/stream", "type": "uri"}},
    )
    assert result["instance_id"] == 42
    assert "run_id" in result


async def test_start_raises_on_missing_pipeline_name():
    shim = _make_shim()
    params = MagicMock()
    params.model_dump.return_value = {
        "pipeline_name": "",
        "camera_id": "rtsp://cam/stream",
        "parameters": {},
    }
    with pytest.raises(ValueError, match="pipeline_name"):
        await shim.start(params)


async def test_start_raises_on_api_failure():
    shim = _make_shim()
    shim._api.start_run = AsyncMock(return_value=None)

    params = MagicMock()
    params.model_dump.return_value = {
        "pipeline_name": "defect",
        "pipeline_version": "1",
        "camera_id": "rtsp://x/y",
        "parameters": {},
    }
    with pytest.raises(RuntimeError):
        await shim.start(params)


# ── stop_run ──────────────────────────────────────────────────────────────────

async def test_stop_run_by_run_id_found_in_cache():
    shim = _make_shim()
    shim._api.stop_run = AsyncMock(return_value=True)
    shim._api.start_run = AsyncMock(return_value={"instance_id": 5})

    params = MagicMock()
    params.model_dump.return_value = {
        "pipeline_name": "pd",
        "pipeline_version": "1",
        "camera_id": "rtsp://c/s",
        "parameters": {},
    }
    r = await shim.start(params)
    ok = await shim.stop_run(r["run_id"])
    assert ok is True


async def test_stop_run_parses_run_id_without_cache():
    shim = _make_shim()
    shim._api.stop_run = AsyncMock(return_value=True)
    ok = await shim.stop_run("my_pipeline/2/99")
    shim._api.stop_run.assert_called_once_with("my_pipeline", "2", "99")
    assert ok is True


async def test_stop_run_unknown_format_returns_false():
    shim = _make_shim()
    ok = await shim.stop_run("bad_run_id")
    assert ok is False


# ── deliver (no-op) ──────────────────────────────────────────────────────────

async def test_deliver_returns_none():
    shim = _make_shim()
    from plugin.core.models.domain import MetadataEvent
    event = MagicMock(spec=MetadataEvent)
    event.event_id = "test-evt"
    result = await shim.deliver(event, "/tmp/clip.mp4")
    assert result is None
