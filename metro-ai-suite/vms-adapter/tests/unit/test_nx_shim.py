"""Unit tests for the Nx Witness single-shim using standard /rest/v4 endpoints."""

import pytest

from plugin.core.config import NvrAuthConfig, NvrInstanceConfig
from vms_shim.nxwitness.shim import NxWitnessVmsShim


@pytest.fixture
def nx_config() -> NvrInstanceConfig:
    return NvrInstanceConfig(
        name="nx-test", vendor="nx_witness",
        base_url="https://localhost:7001",
        auth=NvrAuthConfig(username="admin", password="test", auth_type="digest"),
    )


def test_initial_state(nx_config):
    shim = NxWitnessVmsShim(nx_config)
    assert shim.is_connected() is False


@pytest.mark.asyncio
async def test_get_live_stream_url_includes_onvif_replay(nx_config):
    shim = NxWitnessVmsShim(nx_config)
    url = await shim.get_live_stream_url("nx:device-1")
    assert url == "rtsp://admin:test@localhost:7001/device-1?onvif_replay=true"


@pytest.mark.asyncio
async def test_unsupported_when_disconnected(nx_config):
    shim = NxWitnessVmsShim(nx_config)
    cr = await shim.set_bookmark("nx:cam1", __import__("datetime").datetime.utcnow(), "x")
    assert cr.status == "unsupported"


@pytest.mark.asyncio
async def test_acknowledge_is_unsupported(nx_config):
    """Standard /rest/v4 has no event-acknowledge endpoint."""
    shim = NxWitnessVmsShim(nx_config)
    cr = await shim.acknowledge_event("nx:cam1", "evt1")
    assert cr.status == "unsupported"


class _FakeResp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): return None
    def json(self): return self._p


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        return _FakeResp(self.payload)


@pytest.mark.asyncio
async def test_discover_cameras_uses_rest_v4_devices(nx_config):
    shim = NxWitnessVmsShim(nx_config)
    fake = _FakeClient([
        {"id": "device-1", "name": "Front Door", "url": "rtsp://nx/front-door",
         "status": "Online", "deviceType": "Camera"},
        {"id": "device-2", "name": "Speaker",
         "status": "Online", "deviceType": "IoModule"},
    ])
    shim._client = fake
    cams = await shim.discover_cameras()
    assert fake.calls == [("/rest/v4/devices", None)]
    assert len(cams) == 1
    assert cams[0].camera_id == "nx:device-1"
