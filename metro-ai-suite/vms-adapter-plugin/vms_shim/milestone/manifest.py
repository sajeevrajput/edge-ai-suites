# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""IVA app registration manifests for Milestone AI Bridge.

An IVA (Intelligent Video Analytics) app makes itself known to AI Bridge — and
therefore to XProtect Management Client — through the GraphQL ``register``
mutation. The mutation input declares one entry per app plus the *topics* it
offers:

* ``eventTopics``    - discrete occurrences (``ANALYTICS_EVENT``)
* ``metadataTopics`` - object metadata (``ONVIF_ANALYTICS_FRAME`` here)
* ``videoTopics``    - processed video pushed back to XProtect. **Deliberately
  not declared**: each such stream consumes an XProtect device license.

**One AI Bridge app per Analytics App.** Loitering Detection and Live Video
Captioning register as separate apps so an operator enables them independently
in Management Client. Apps are discovered from the ``analytics_apps`` list in
``config.yaml`` and each one's topic is chosen from its ``type`` — see
:data:`TYPE_TOPIC_MAP`.

The ``url`` fields point at the **VAP dashboard**: Management Client renders them
in an embedded browser as the app's configuration page. They are *not* data
sinks — an IVA app pushes metadata by resolving ``topicAvailability.rest`` over
GraphQL after registration.

App GUIDs are stable across restarts, which is what makes re-registration
idempotent rather than duplicate-creating: an explicit GUID from
``vendor_options.app_ids`` wins, otherwise one is derived deterministically from
the VMS instance and analytics app id.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import quote, urlencode

DEFAULT_MANIFEST_PATH = Path(__file__).parent / "aib_manifest.json"

# Fields whose values are GraphQL enums and must be emitted unquoted.
_ENUM_FIELDS = frozenset({"eventFormat", "metadataFormat", "videoFormat"})

SUPPORTED_METADATA_FORMATS = frozenset(
    {"ONVIF_ANALYTICS", "ONVIF_ANALYTICS_FRAME", "DEEPSTREAM_MINIMAL"},
)

DEFAULT_METADATA_FORMAT = "ONVIF_ANALYTICS_FRAME"
DEFAULT_EVENT_FORMAT = "ANALYTICS_EVENT"

# Stable namespace for uuid5-derived app GUIDs. Must never change: doing so
# would orphan every previously registered app in XProtect.
APP_ID_NAMESPACE = uuid.UUID("1b2f9a30-9d5e-5a7c-9c1f-2d4a6b8e0c31")

# Analytics app ``type`` (from ``config.yaml``) → the AI Bridge topic it declares.
#
# * ``object_detection`` (e.g. Loitering Detection) emits object bounding boxes,
#   which XProtect ingests as a **metadata** topic in ONVIF frame format. This is
#   what drives bounding-box overlays and Metadata Search.
# * ``live_captioning`` emits free-text captions, which have no bounding boxes and
#   so are delivered as discrete **analytics events**.
TYPE_TOPIC_MAP: dict[str, dict[str, str]] = {
    "object_detection": {
        "topic_list": "metadataTopics",
        "format_field": "metadataFormat",
        "format": DEFAULT_METADATA_FORMAT,
        "suffix": "metadata",
        "description": "Object detections as ONVIF frame metadata",
    },
    "live_captioning": {
        "topic_list": "eventTopics",
        "format_field": "eventFormat",
        "format": DEFAULT_EVENT_FORMAT,
        "suffix": "events",
        "description": "Scene captions as analytics events",
    },
}

DEFAULT_MANUFACTURER = "Intel Corporation"
DEFAULT_APP_VERSION = "1.0"

DEFAULT_CONTEXT: dict[str, str] = {
    "APP_ID": "6f2a0c1e-6f3d-4e5a-9a52-3f6b9c1d8e40",
    "APP_NAME": "Intel VMS Adapter Plugin",
    "APP_VERSION": DEFAULT_APP_VERSION,
    "APP_DESCRIPTION": (
        "Bridges Intel Edge AI analytics applications with Milestone XProtect."
    ),
    "MANUFACTURER_NAME": DEFAULT_MANUFACTURER,
    "EVENT_TOPIC": "vap-analytics-events",
    "METADATA_TOPIC": "vap-onvif-frame",
    "METADATA_FORMAT": DEFAULT_METADATA_FORMAT,
}


def derive_app_guid(vms_name: str, analytics_app_id: str) -> str:
    """Derive a stable app GUID from the VMS instance and analytics app id.

    Deterministic, so restarting VAP re-registers the same app rather than
    creating a duplicate. Scoped by ``vms_name`` so two VAP instances pointed at
    the same AI Bridge do not collide.
    """
    return str(uuid.uuid5(APP_ID_NAMESPACE, f"{vms_name}:{analytics_app_id}"))


def resolve_app_guid(
    vms_name: str, analytics_app_id: str, vendor_options: dict[str, Any],
) -> str:
    """Return the configured GUID for an app, else a derived one.

    Raises:
        ValueError: if a configured value is not a valid GUID — AI Bridge rejects
            malformed GUIDs, so failing here gives a clearer error.
    """
    configured = (vendor_options.get("app_ids") or {}).get(analytics_app_id)
    if configured in (None, ""):
        return derive_app_guid(vms_name, analytics_app_id)
    try:
        return str(uuid.UUID(str(configured)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(
            f"vendor_options.app_ids['{analytics_app_id}'] is not a valid GUID: "
            f"{configured!r}",
        ) from exc


def topic_name(analytics_app_id: str, suffix: str) -> str:
    """Build a topic name that is unique across apps sharing one AI Bridge."""
    return f"{analytics_app_id}-{suffix}"


def _dashboard_url(base: str, analytics_app_id: str, topic: str | None = None) -> str:
    """Build the Management Client config-page URL for an app or one of its topics."""
    params = {"app": analytics_app_id}
    if topic:
        params["topic"] = topic
    return f"{base}?{urlencode(params, quote_via=quote)}"


def build_topics(
    analytics_app_id: str,
    app_type: str,
    dashboard_url: str,
    metadata_format: str = DEFAULT_METADATA_FORMAT,
) -> dict[str, list[dict[str, Any]]]:
    """Build the AI Bridge topic list for one analytics app, keyed off its type.

    ``object_detection`` yields a metadata topic (ONVIF frame format);
    ``live_captioning`` yields an event topic. An unrecognised type yields ``{}``
    so a newly added analytics app cannot break registration for the others —
    the caller logs it.

    Video topics are never produced: each one consumes an XProtect device
    license per stream.
    """
    spec = TYPE_TOPIC_MAP.get(app_type)
    if spec is None:
        return {}

    fmt = metadata_format if spec["format_field"] == "metadataFormat" else spec["format"]
    entry: dict[str, Any] = {
        "url": _dashboard_url(dashboard_url, analytics_app_id, spec["suffix"]),
        "name": topic_name(analytics_app_id, spec["suffix"]),
        "description": spec["description"],
        spec["format_field"]: fmt,
    }
    return {spec["topic_list"]: [entry]}


def build_app_entry(
    vms_name: str,
    analytics_app_id: str,
    app_type: str,
    display_name: str,
    vendor_options: dict[str, Any],
) -> dict[str, Any] | None:
    """Build one ``apps`` entry for an analytics app.

    Returns ``None`` for an analytics app type that has no AI Bridge topic
    mapping — registering it would only create an empty node in Management
    Client.

    Raises:
        ValueError: if ``dashboard_url`` is missing or configuration is invalid.
    """
    dashboard_url = require_dashboard_url(vendor_options)
    metadata_format = validated_metadata_format(vendor_options)

    topics = build_topics(analytics_app_id, app_type, dashboard_url, metadata_format)
    if not topics:
        return None

    label = display_name or analytics_app_id
    entry: dict[str, Any] = {
        "id": resolve_app_guid(vms_name, analytics_app_id, vendor_options),
        "url": _dashboard_url(dashboard_url, analytics_app_id),
        "name": label,
        "version": str(vendor_options.get("app_version") or DEFAULT_APP_VERSION),
        "description": f"{label}, delivered by the Intel VMS Adapter Plugin",
        "manufacturer": {
            "name": str(
                vendor_options.get("manufacturer_name") or DEFAULT_MANUFACTURER,
            ),
        },
    }
    entry.update(topics)
    return entry


def build_apps(
    vms_name: str,
    analytics_apps: list[dict[str, Any]],
    vendor_options: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the ``apps`` list from the configured analytics apps.

    Args:
        vms_name: VMS instance name, used to scope derived GUIDs.
        analytics_apps: dicts with ``app_id``, ``type`` and ``display_name``,
            as read from the ``analytics_apps`` list in ``config.yaml``.
        vendor_options: this VMS instance's ``vendor_options``.
    """
    apps: list[dict[str, Any]] = []
    for app in analytics_apps:
        entry = build_app_entry(
            vms_name,
            str(app.get("app_id") or ""),
            str(app.get("type") or ""),
            str(app.get("display_name") or ""),
            vendor_options,
        )
        if entry:
            apps.append(entry)
    return apps


def require_dashboard_url(vendor_options: dict[str, Any]) -> str:
    """Return the VAP dashboard base URL Management Client should render.

    Falls back to ``app_base_url`` for backwards compatibility.
    """
    raw = (
        vendor_options.get("dashboard_url")
        or vendor_options.get("app_base_url")
        or ""
    )
    url = str(raw).strip().rstrip("/")
    if not url:
        raise ValueError(
            "milestone vendor_options.dashboard_url is required — it is the VAP "
            "dashboard URL (e.g. https://<host>:3443) that XProtect Management "
            "Client renders as the app configuration page, so it must be "
            "resolvable from the XProtect host",
        )
    return url


def validated_metadata_format(vendor_options: dict[str, Any]) -> str:
    """Return the configured metadata format, rejecting unknown values."""
    fmt = str(vendor_options.get("metadata_format") or DEFAULT_METADATA_FORMAT)
    if fmt not in SUPPORTED_METADATA_FORMATS:
        raise ValueError(
            f"unsupported metadata_format '{fmt}'; "
            f"expected one of {sorted(SUPPORTED_METADATA_FORMATS)}",
        )
    return fmt


def build_context(vendor_options: dict[str, Any]) -> dict[str, str]:
    """Merge ``vendor_options`` over :data:`DEFAULT_CONTEXT` for the static template.

    Used only by :func:`load_manifest`, the fallback path for deployments that
    pin a hand-written manifest instead of deriving one from analytics apps.

    Raises:
        ValueError: if the dashboard URL is missing or the metadata format is
            not one AI Bridge understands.
    """
    ctx = dict(DEFAULT_CONTEXT)
    for key in DEFAULT_CONTEXT:
        value = vendor_options.get(key.lower())
        if value not in (None, ""):
            ctx[key] = str(value)
    ctx["APP_BASE_URL"] = require_dashboard_url(vendor_options)
    ctx["METADATA_FORMAT"] = validated_metadata_format(
        {"metadata_format": ctx["METADATA_FORMAT"]},
    )
    return ctx


def load_manifest(
    vendor_options: dict[str, Any], manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load and resolve the static manifest template into a plain dict.

    Args:
        vendor_options: ``VmsInstanceConfig.vendor_options`` for this instance.
        manifest_path: Override for the bundled template.

    Raises:
        ValueError: on a missing placeholder or invalid manifest content.
    """
    path = Path(manifest_path) if manifest_path else DEFAULT_MANIFEST_PATH
    raw = path.read_text(encoding="utf-8")
    try:
        resolved = Template(raw).substitute(build_context(vendor_options))
    except KeyError as exc:
        raise ValueError(f"unresolved placeholder in {path}: {exc}") from exc

    manifest = json.loads(resolved)
    if not isinstance(manifest, dict) or not manifest.get("apps"):
        raise ValueError(f"manifest {path} must contain a non-empty 'apps' list")
    return manifest


def to_graphql_literal(value: Any, field_name: str | None = None) -> str:
    """Serialize a Python value into GraphQL literal syntax.

    Differs from JSON in two ways that AI Bridge cares about: object keys are
    unquoted, and values of :data:`_ENUM_FIELDS` are emitted as bare enum tokens
    rather than strings.
    """
    if isinstance(value, dict):
        body = " ".join(f"{k}: {to_graphql_literal(v, k)}" for k, v in value.items())
        return f"{{ {body} }}"
    if isinstance(value, (list, tuple)):
        body = ", ".join(to_graphql_literal(v, field_name) for v in value)
        return f"[ {body} ]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if field_name in _ENUM_FIELDS:
        return str(value)
    return json.dumps(str(value))


def apps_literal(manifest: dict[str, Any] | list[dict[str, Any]]) -> str:
    """Render the ``apps: [...]`` fragment spliced into the register mutation.

    Accepts either a full manifest dict or a bare list of app entries.
    """
    apps = manifest["apps"] if isinstance(manifest, dict) else manifest
    return f"apps: {to_graphql_literal(apps, 'apps')}"
