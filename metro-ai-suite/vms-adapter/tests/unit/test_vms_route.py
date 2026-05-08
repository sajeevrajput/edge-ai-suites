"""Unit tests for POST /v1/vms/{name}/register with Nx analytics integration."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugin.core.api.deps import get_db_session, get_nvr_shim_sets
from plugin.core.config import NvrAuthConfig, NvrInstanceConfig
from plugin.core.main import create_app
from fastapi.testclient import TestClient


_SAMPLE_MANIFESTS = {
    "integrationManifest": {
        "id": "test.integration",
        "name": "Test Integration",
        "version": "1.0.0",
    },
    "engineManifest": {
        "typeLibrary": {
            "objectTypes": [{"id": "test.obj", "name": "Object"}],
        }
    },
    "deviceAgentManifest": {"supportedTypes": []},
    "pinCode": "1234",
}


def _make_nx_shim_set(name="nx-main", manifest_path: str | None = None):
    config = NvrInstanceConfig(
        name=name,
        vendor="nx_witness",
        base_url="https://localhost:7001",
        auth=NvrAuthConfig(username="admin", password="test"),
        analytics_manifest_path=manifest_path,
    )
    shim = AsyncMock()
    shim.register_analytics = AsyncMock(return_value={
        "status": "approved",
        "username": "integration_user",
        "password": "secret",
        "request_id": "req-123",
    })
    return SimpleNamespace(name=name, config=config, vms_shim=shim)


def _make_non_nx_shim_set(name="frigate-main"):
    config = SimpleNamespace(vendor="frigate", analytics_manifest_path=None)
    shim = AsyncMock()
    shim.register_analytics = AsyncMock(return_value={"status": "ok"})
    return SimpleNamespace(name=name, config=config, vms_shim=shim)


@pytest.fixture
def client_factory():
    """Returns a factory to build a TestClient with injected shim sets and DB."""
    import contextlib

    @contextlib.contextmanager
    def _make(shim_sets, db_integration=None):
        app = create_app()

        async def override_shims():
            return shim_sets

        mock_db = AsyncMock()

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_nvr_shim_sets] = override_shims
        app.dependency_overrides[get_db_session] = override_db

        # Patch repository calls
        with patch("plugin.core.api.routes.vms.repo") as mock_repo:
            mock_repo.get_nx_integration = AsyncMock(return_value=db_integration)
            mock_repo.upsert_nx_integration = AsyncMock(return_value=MagicMock(
                model_dump=lambda **kw: {
                    "vms_name": "nx-main",
                    "status": "approved",
                    "nx_username": "integration_user",
                    "nx_password": "secret",
                    "nx_request_id": "req-123",
                    "integration_manifest": _SAMPLE_MANIFESTS["integrationManifest"],
                    "engine_manifest": _SAMPLE_MANIFESTS["engineManifest"],
                    "device_agent_manifest": None,
                    "registered_at": None,
                }
            ))
            yield TestClient(app, raise_server_exceptions=False), mock_repo

    return _make


# ── Tests ────────────────────────────────────────────────────────────────────

def test_register_non_nx_vendor_uses_shim_directly(client_factory):
    """Non-Nx vendors bypass DB and call shim directly."""
    ss = _make_non_nx_shim_set()
    with client_factory([ss]) as (client, _):
        resp = client.post("/v1/vms/frigate-main/register", json={"manifest": {}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    ss.vms_shim.register_analytics.assert_awaited_once()


def test_register_vms_not_found(client_factory):
    with client_factory([]) as (client, _):
        resp = client.post("/v1/vms/unknown/register", json={"manifest": {}})
    assert resp.status_code == 404


def test_register_nx_returns_cached_if_already_approved(client_factory):
    """If DB already has an approved integration, skip Nx and return cached."""
    from plugin.core.models.domain import NxAnalyticsIntegration
    from datetime import datetime, timezone

    cached = NxAnalyticsIntegration(
        id="some-uuid",
        vms_name="nx-main",
        integration_manifest={},
        engine_manifest={},
        nx_username="cached_user",
        nx_password="cached_pass",
        nx_request_id="req-old",
        status="approved",
        registered_at=datetime.now(timezone.utc),
    )
    ss = _make_nx_shim_set()
    with client_factory([ss], db_integration=cached) as (client, mock_repo):
        resp = client.post("/v1/vms/nx-main/register", json={"manifest": {}})

    assert resp.status_code == 200
    data = resp.json()
    assert data["nx_username"] == "cached_user"
    # Shim should NOT have been called
    ss.vms_shim.register_analytics.assert_not_awaited()


def test_register_nx_with_inline_manifests(client_factory):
    """Structured manifests in request body trigger Phase 1 and persist to DB."""
    ss = _make_nx_shim_set()
    with client_factory([ss]) as (client, mock_repo):
        resp = client.post("/v1/vms/nx-main/register", json={
            "integration_manifest": _SAMPLE_MANIFESTS["integrationManifest"],
            "engine_manifest": _SAMPLE_MANIFESTS["engineManifest"],
            "device_agent_manifest": _SAMPLE_MANIFESTS["deviceAgentManifest"],
            "pin_code": "1234",
        })

    assert resp.status_code == 200
    ss.vms_shim.register_analytics.assert_awaited_once()
    called_manifest = ss.vms_shim.register_analytics.call_args[0][0]
    assert called_manifest["integrationManifest"]["id"] == "test.integration"
    assert called_manifest["pinCode"] == "1234"
    mock_repo.upsert_nx_integration.assert_awaited_once()


def test_register_nx_with_manifest_file_path(client_factory, tmp_path):
    """Manifest loaded from config YAML analytics_manifest_path."""
    manifest_file = tmp_path / "nx_manifest.json"
    manifest_file.write_text(json.dumps(_SAMPLE_MANIFESTS))

    ss = _make_nx_shim_set(manifest_path=str(manifest_file))
    with client_factory([ss]) as (client, mock_repo):
        # No manifests in body — should load from file
        resp = client.post("/v1/vms/nx-main/register", json={"manifest": {}})

    assert resp.status_code == 200
    ss.vms_shim.register_analytics.assert_awaited_once()


def test_register_nx_no_manifest_anywhere_returns_422(client_factory):
    """No manifest in body and no manifest_path → 422."""
    ss = _make_nx_shim_set(manifest_path=None)
    with client_factory([ss]) as (client, _):
        resp = client.post("/v1/vms/nx-main/register", json={"manifest": {}})
    assert resp.status_code == 422


def test_register_nx_manifest_file_not_found_returns_422(client_factory):
    """analytics_manifest_path points to non-existent file → 422."""
    ss = _make_nx_shim_set(manifest_path="/does/not/exist.json")
    with client_factory([ss]) as (client, _):
        resp = client.post("/v1/vms/nx-main/register", json={"manifest": {}})
    assert resp.status_code == 422


def test_register_nx_shim_error_returns_502(client_factory):
    """Shim returning status=error → 502."""
    ss = _make_nx_shim_set()
    ss.vms_shim.register_analytics = AsyncMock(return_value={
        "status": "error",
        "reason": "create_integration_request_failed",
    })
    with client_factory([ss]) as (client, _):
        resp = client.post("/v1/vms/nx-main/register", json={
            "integration_manifest": _SAMPLE_MANIFESTS["integrationManifest"],
            "engine_manifest": _SAMPLE_MANIFESTS["engineManifest"],
        })
    assert resp.status_code == 502
