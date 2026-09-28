# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Milestone AI Bridge shim.

Covers camera discovery and the auto-registration of Analytics Apps (Loitering
Detection, Live Video Captioning) as individual AI Bridge IVA apps.
"""

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from plugin.core.config import VmsInstanceConfig
from vms_shim.milestone.manifest import (
    apps_literal,
    build_app_entry,
    build_apps,
    build_topics,
    derive_app_guid,
    load_manifest,
    resolve_app_guid,
    to_graphql_literal,
    topic_name,
)
from vms_shim.milestone.shim import MilestoneVmsShim

GRAPHQL_URL = "http://aib:4000/api/bridge/graphql"
DASHBOARD_URL = "https://vap-host:3443"

DEVICE_ID = "2884f669-3b7a-4fbc-8f01-5cbfa8d3279e"
STREAM_ID = "28dc44c3-079e-4c94-8ec9-60363451eb40"
COMPOSITE_ID = f"{DEVICE_ID}/{STREAM_ID}"
RTSP_URL = f"rtsp://aib:8554/{COMPOSITE_ID}"

# Mirrors the analytics_apps entries in config.yaml.
LD_APP = {
    "app_id": "dls_vision",
    "type": "object_detection",
    "display_name": "Loitering Detection",
}
LVC_APP = {
    "app_id": "live_captioning",
    "type": "live_captioning",
    "display_name": "Live Video Captioning",
}


@pytest.fixture
def milestone_config() -> VmsInstanceConfig:
    return VmsInstanceConfig(
        name="milestone-test",
        vendor="milestone",
        base_url="http://aib:4000",
        vendor_options={
            "graphql_url": GRAPHQL_URL,
            "dashboard_url": DASHBOARD_URL,
        },
    )


def _fake_orchestrator(*app_configs) -> SimpleNamespace:
    """Build a stand-in orchestrator carrying an ``analytics_apps`` config list."""
    apps = [SimpleNamespace(**cfg) for cfg in app_configs]
    return SimpleNamespace(config=SimpleNamespace(analytics_apps=apps))


async def _connected_shim(config: VmsInstanceConfig, handler) -> MilestoneVmsShim:
    """Build a shim whose HTTP client is backed by ``handler``."""
    shim = MilestoneVmsShim(config)
    shim._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    shim._connected = True
    shim._vms_ids = ["vms-1"]
    return shim


# ── Configuration ────────────────────────────────────────────────────────────


def test_camera_id_prefix(milestone_config):
    assert MilestoneVmsShim(milestone_config).camera_id_prefix == "milestone:"


def test_graphql_url_defaults_to_base_url():
    config = VmsInstanceConfig(
        name="m", vendor="milestone", base_url="http://aib:4000/",
        vendor_options={"dashboard_url": DASHBOARD_URL},
    )
    assert MilestoneVmsShim(config)._graphql_url == GRAPHQL_URL


def test_vendor_registered_in_factory():
    from plugin.core.factory import _VMS_REGISTRY
    assert _VMS_REGISTRY["milestone"] is MilestoneVmsShim


def test_configured_vms_id_is_used_verbatim():
    config = VmsInstanceConfig(
        name="m", vendor="milestone",
        vendor_options={"graphql_url": GRAPHQL_URL, "vms_id": "pinned"},
    )
    assert MilestoneVmsShim(config)._vms_ids == ["pinned"]


# ── App GUIDs ────────────────────────────────────────────────────────────────


def test_derived_guid_is_stable_and_scoped():
    first = derive_app_guid("milestone-main", "dls_vision")
    assert first == derive_app_guid("milestone-main", "dls_vision")
    # Different app or different VMS instance ⇒ different GUID.
    assert first != derive_app_guid("milestone-main", "live_captioning")
    assert first != derive_app_guid("milestone-other", "dls_vision")


def test_configured_guid_overrides_derived():
    explicit = "11111111-2222-3333-4444-555555555555"
    guid = resolve_app_guid("m", "dls_vision", {"app_ids": {"dls_vision": explicit}})
    assert guid == explicit


def test_configured_guid_must_be_a_valid_uuid():
    with pytest.raises(ValueError, match="not a valid GUID"):
        resolve_app_guid("m", "dls_vision", {"app_ids": {"dls_vision": "nope"}})


def test_unlisted_app_falls_back_to_derived_guid():
    opts = {"app_ids": {"other": "11111111-2222-3333-4444-555555555555"}}
    assert resolve_app_guid("m", "dls_vision", opts) == derive_app_guid("m", "dls_vision")


# ── Topic mapping (driven by the analytics app's config `type`) ──────────────


def test_object_detection_maps_to_onvif_frame_metadata_topic():
    topics = build_topics("dls_vision", "object_detection", DASHBOARD_URL)
    assert "eventTopics" not in topics
    assert topics["metadataTopics"][0]["name"] == "dls_vision-metadata"
    assert topics["metadataTopics"][0]["metadataFormat"] == "ONVIF_ANALYTICS_FRAME"


def test_live_captioning_maps_to_analytics_event_topic():
    topics = build_topics("live_captioning", "live_captioning", DASHBOARD_URL)
    assert "metadataTopics" not in topics
    assert topics["eventTopics"][0]["name"] == "live_captioning-events"
    assert topics["eventTopics"][0]["eventFormat"] == "ANALYTICS_EVENT"


def test_unknown_app_type_maps_to_no_topic():
    assert build_topics("mystery", "something_new", DASHBOARD_URL) == {}
    assert build_topics("mystery", "", DASHBOARD_URL) == {}


def test_topic_names_are_unique_across_apps():
    assert topic_name("dls_vision", "metadata") != topic_name("live_captioning", "metadata")


def test_metadata_format_is_configurable():
    topics = build_topics(
        "dls_vision", "object_detection", DASHBOARD_URL,
        metadata_format="ONVIF_ANALYTICS",
    )
    assert topics["metadataTopics"][0]["metadataFormat"] == "ONVIF_ANALYTICS"


def test_event_format_is_not_affected_by_metadata_format():
    topics = build_topics(
        "live_captioning", "live_captioning", DASHBOARD_URL,
        metadata_format="ONVIF_ANALYTICS",
    )
    assert topics["eventTopics"][0]["eventFormat"] == "ANALYTICS_EVENT"


# ── App entries ──────────────────────────────────────────────────────────────


def test_app_entry_points_urls_at_the_dashboard(milestone_config):
    entry = build_app_entry(
        "milestone-test", "dls_vision", "object_detection", "Loitering Detection",
        milestone_config.vendor_options,
    )
    assert entry["url"] == f"{DASHBOARD_URL}?app=dls_vision"
    assert entry["name"] == "Loitering Detection"
    assert entry["id"] == derive_app_guid("milestone-test", "dls_vision")
    assert entry["metadataTopics"][0]["url"] == (
        f"{DASHBOARD_URL}?app=dls_vision&topic=metadata"
    )
    # Video topics consume an XProtect device license — never declared.
    assert "videoTopics" not in entry


def test_app_with_unmapped_type_is_skipped(milestone_config):
    entry = build_app_entry(
        "m", "mystery", "something_new", "Mystery", milestone_config.vendor_options,
    )
    assert entry is None


def test_build_apps_emits_one_entry_per_configured_app(milestone_config):
    apps = build_apps(
        "milestone-test",
        [LD_APP, LVC_APP, {"app_id": "x", "type": "unknown", "display_name": "X"}],
        milestone_config.vendor_options,
    )
    assert [a["name"] for a in apps] == ["Loitering Detection", "Live Video Captioning"]
    assert len({a["id"] for a in apps}) == 2
    assert "metadataTopics" in apps[0]
    assert "eventTopics" in apps[1]


def test_dashboard_url_is_required():
    with pytest.raises(ValueError, match="dashboard_url"):
        build_app_entry("m", "a", "object_detection", "A", {})


def test_app_base_url_still_accepted_for_backwards_compatibility():
    entry = build_app_entry(
        "m", "a", "object_detection", "A", {"app_base_url": "https://legacy:8080"},
    )
    assert entry["url"].startswith("https://legacy:8080?")


def test_unknown_metadata_format_is_rejected():
    with pytest.raises(ValueError, match="unsupported metadata_format"):
        build_app_entry(
            "m", "a", "object_detection", "A",
            {"dashboard_url": DASHBOARD_URL, "metadata_format": "BOGUS"},
        )


# ── GraphQL literal rendering ────────────────────────────────────────────────


def test_graphql_literal_leaves_enums_unquoted():
    literal = to_graphql_literal(
        {"name": "t", "metadataFormat": "ONVIF_ANALYTICS_FRAME", "n": 1, "b": True},
    )
    assert 'name: "t"' in literal
    assert "metadataFormat: ONVIF_ANALYTICS_FRAME" in literal
    assert '"ONVIF_ANALYTICS_FRAME"' not in literal
    assert "n: 1" in literal
    assert "b: true" in literal


def test_graphql_literal_escapes_strings():
    assert to_graphql_literal({"name": 'a"b'}) == '{ name: "a\\"b" }'


def test_apps_literal_accepts_a_bare_list(milestone_config):
    apps = build_apps("m", [LD_APP], milestone_config.vendor_options)
    literal = apps_literal(apps)
    assert literal.startswith("apps: [ {")
    assert apps_literal({"apps": apps}) == literal


def test_static_template_still_loads():
    manifest = load_manifest({"dashboard_url": DASHBOARD_URL})
    assert "${" not in str(manifest)
    assert manifest["apps"][0]["url"] == DASHBOARD_URL


# ── Discovery ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_cameras_splits_composite_stream_id(milestone_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"cameras": [{
            "name": "AXIS Q6054",
            "videoStreams": [{
                "id": COMPOSITE_ID,
                "name": "Video stream 1",
                "streamAvailability": {"rtsp": RTSP_URL},
            }],
        }]}})

    shim = await _connected_shim(milestone_config, handler)
    cams = await shim.discover_cameras()

    assert len(cams) == 1
    cam = cams[0]
    assert cam.camera_id == f"milestone:{COMPOSITE_ID}"
    assert cam.vendor == "milestone"
    assert cam.stream_url == RTSP_URL
    assert cam.status == "online"
    assert cam.vendor_meta["device_id"] == DEVICE_ID
    assert cam.vendor_meta["stream_id"] == STREAM_ID
    assert cam.name == "AXIS Q6054 - Video stream 1"


@pytest.mark.asyncio
async def test_discover_cameras_marks_streams_without_rtsp_offline(milestone_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"cameras": [{
            "name": "cam", "videoStreams": [
                {"id": COMPOSITE_ID, "name": "s1", "streamAvailability": {}},
                {"name": "no id"},
            ],
        }]}})

    shim = await _connected_shim(milestone_config, handler)
    cams = await shim.discover_cameras()

    assert len(cams) == 1
    assert cams[0].status == "offline"
    assert cams[0].stream_url is None


@pytest.mark.asyncio
async def test_graphql_errors_are_not_treated_as_success(milestone_config):
    def handler(request: httpx.Request) -> httpx.Response:
        # GraphQL reports failures inside a 200 response.
        return httpx.Response(200, json={"errors": [{"message": "boom"}]})

    shim = await _connected_shim(milestone_config, handler)
    assert await shim.discover_cameras() == []


@pytest.mark.asyncio
async def test_get_live_stream_url_uses_discovery_cache(milestone_config):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": {"cameras": [{
            "name": "cam",
            "videoStreams": [{
                "id": COMPOSITE_ID, "name": "s1",
                "streamAvailability": {"rtsp": RTSP_URL},
            }],
        }]}})

    shim = await _connected_shim(milestone_config, handler)
    await shim.discover_cameras()
    url = await shim.get_live_stream_url(f"milestone:{COMPOSITE_ID}")

    assert url == RTSP_URL
    assert len(calls) == 1  # served from cache, no second round-trip


@pytest.mark.asyncio
async def test_connect_collects_every_attached_vms(milestone_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"about": {
            "videoManagementSystems": [{"id": "xprotect-1"}, {"id": "xprotect-2"}],
        }}})

    shim = MilestoneVmsShim(milestone_config)
    shim._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    data = await shim._graphql("query { about { videoManagementSystems { id } } }")
    ids = [s["id"] for s in data["about"]["videoManagementSystems"]]
    assert ids == ["xprotect-1", "xprotect-2"]
    await shim.disconnect()


@pytest.mark.asyncio
async def test_connect_without_graphql_url_stays_disconnected():
    config = VmsInstanceConfig(name="m", vendor="milestone")
    shim = MilestoneVmsShim(config)
    await shim.connect()
    assert shim.is_connected() is False


# ── Auto-registration ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_startup_registers_each_analytics_app(milestone_config):
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content.decode())
        return httpx.Response(200, json={"data": {"register": {"id": "vms-1"}}})

    shim = await _connected_shim(milestone_config, handler)
    await shim.on_startup(_fake_orchestrator(LD_APP, LVC_APP))

    assert len(bodies) == 1  # one mutation carrying both apps
    body = bodies[0]
    assert "Loitering Detection" in body
    assert "Live Video Captioning" in body
    # Object detection → ONVIF frame metadata; captioning → analytics events.
    assert "dls_vision-metadata" in body
    assert "metadataFormat: ONVIF_ANALYTICS_FRAME" in body
    assert "live_captioning-events" in body
    assert "eventFormat: ANALYTICS_EVENT" in body
    assert DASHBOARD_URL in body


@pytest.mark.asyncio
async def test_registers_with_every_discovered_vms(milestone_config):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        return httpx.Response(200, json={"data": {"register": {"id": "ok"}}})

    shim = await _connected_shim(milestone_config, handler)
    shim._vms_ids = ["vms-a", "vms-b"]
    out = await shim.register_analytics({"analytics_apps": [LD_APP]})

    assert out["status"] == "registered"
    assert out["vms_ids"] == ["vms-a", "vms-b"]
    assert len(seen) == 2
    assert 'id: \\"vms-a\\"' in seen[0]
    assert 'id: \\"vms-b\\"' in seen[1]


@pytest.mark.asyncio
async def test_partial_registration_failure_is_reported(milestone_config):
    def handler(request: httpx.Request) -> httpx.Response:
        if "vms-b" in request.content.decode():
            return httpx.Response(200, json={"errors": [{"message": "denied"}]})
        return httpx.Response(200, json={"data": {"register": {"id": "ok"}}})

    shim = await _connected_shim(milestone_config, handler)
    shim._vms_ids = ["vms-a", "vms-b"]
    out = await shim.register_analytics({"analytics_apps": [LD_APP]})

    assert out["status"] == "registered"
    assert out["vms_ids"] == ["vms-a"]
    assert out["failed_vms_ids"] == ["vms-b"]


@pytest.mark.asyncio
async def test_registration_is_idempotent_across_restarts(milestone_config):
    """A restart must reuse the same app GUIDs, not create duplicates."""
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content.decode())
        return httpx.Response(200, json={"data": {"register": {"id": "vms-1"}}})

    orchestrator = _fake_orchestrator(LD_APP)
    for _ in range(2):
        shim = await _connected_shim(milestone_config, handler)
        await shim.on_startup(orchestrator)

    assert bodies[0] == bodies[1]


@pytest.mark.asyncio
async def test_on_startup_without_analytics_apps_does_not_register(milestone_config):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content.decode())
        return httpx.Response(200, json={"data": {"register": {"id": "vms-1"}}})

    shim = await _connected_shim(milestone_config, handler)
    await shim.on_startup(_fake_orchestrator())
    assert calls == []


@pytest.mark.asyncio
async def test_collect_skips_apps_with_an_unmapped_type(milestone_config):
    """An unrecognised analytics app type must not block the others."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"register": {"id": "vms-1"}}})

    orchestrator = _fake_orchestrator(
        LD_APP,
        {"app_id": "mystery", "type": "something_new", "display_name": "Mystery"},
    )

    shim = await _connected_shim(milestone_config, handler)
    apps = shim._collect_analytics_apps(orchestrator)
    assert [a["app_id"] for a in apps] == ["dls_vision"]


@pytest.mark.asyncio
async def test_collect_reads_type_and_display_name_from_config(milestone_config):
    shim = MilestoneVmsShim(milestone_config)
    apps = shim._collect_analytics_apps(_fake_orchestrator(LD_APP, LVC_APP))
    assert apps == [
        {
            "app_id": "dls_vision",
            "type": "object_detection",
            "display_name": "Loitering Detection",
        },
        {
            "app_id": "live_captioning",
            "type": "live_captioning",
            "display_name": "Live Video Captioning",
        },
    ]


@pytest.mark.asyncio
async def test_register_analytics_uses_supplied_apps_manifest(milestone_config):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"data": {"register": {"id": "vms-1"}}})

    shim = await _connected_shim(milestone_config, handler)
    out = await shim.register_analytics(
        {"apps": [{"id": "custom-app", "name": "Custom"}]},
    )

    assert out["app_ids"] == ["custom-app"]
    assert "custom-app" in captured["body"]


@pytest.mark.asyncio
async def test_register_analytics_requires_connection(milestone_config):
    shim = MilestoneVmsShim(milestone_config)
    out = await shim.register_analytics({})
    assert out == {"status": "error", "reason": "not_connected"}


@pytest.mark.asyncio
async def test_register_analytics_requires_a_known_vms(milestone_config):
    shim = await _connected_shim(milestone_config, lambda r: httpx.Response(200))
    shim._vms_ids = []
    out = await shim.register_analytics({})
    assert out["reason"] == "vms_id_unknown"


@pytest.mark.asyncio
async def test_register_analytics_reports_missing_dashboard_url():
    config = VmsInstanceConfig(
        name="m", vendor="milestone",
        vendor_options={"graphql_url": GRAPHQL_URL},
    )
    shim = await _connected_shim(config, lambda r: httpx.Response(200))
    out = await shim.register_analytics({"analytics_apps": [LD_APP]})
    assert out["status"] == "error"
    assert "dashboard_url" in out["reason"]


@pytest.mark.asyncio
async def test_handle_register_reregisters_configured_apps(milestone_config):
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content.decode())
        return httpx.Response(200, json={"data": {"register": {"id": "vms-1"}}})

    shim = await _connected_shim(milestone_config, handler)
    shim._orchestrator = _fake_orchestrator(LD_APP)
    out = await shim.handle_register({}, db=None, vms_name="milestone-test")

    assert out["status"] == "registered"
    assert "dls_vision-metadata" in bodies[0]


# ── Topic sinks ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_topic_sink_url(milestone_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"metadataTopics": [
            {"topicAvailability": {"rest": "http://aib:4000/topics/meta"}},
        ]}})

    shim = await _connected_shim(milestone_config, handler)
    assert await shim.get_topic_sink_url("dls_vision-detections") == (
        "http://aib:4000/topics/meta"
    )


@pytest.mark.asyncio
async def test_get_topic_sink_url_missing_topic(milestone_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"metadataTopics": []}})

    shim = await _connected_shim(milestone_config, handler)
    assert await shim.get_topic_sink_url("nope") is None


# ── Unsupported capabilities ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unsupported_write_operations(milestone_config):
    shim = MilestoneVmsShim(milestone_config)
    cam = f"milestone:{COMPOSITE_ID}"

    assert (await shim.set_bookmark(cam, datetime.now(), "l")).status == "unsupported"
    assert (await shim.trigger_recording(cam)).status == "unsupported"
    assert (await shim.push_label(cam, "e1", ["car"])).status == "unsupported"
    assert (await shim.acknowledge_event(cam, "e1")).status == "unsupported"


@pytest.mark.asyncio
async def test_get_clip_url_returns_none(milestone_config):
    shim = MilestoneVmsShim(milestone_config)
    url = await shim.get_clip_url(
        f"milestone:{COMPOSITE_ID}", datetime(2026, 1, 1), datetime(2026, 1, 2),
    )
    assert url is None
