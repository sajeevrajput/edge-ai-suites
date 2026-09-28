# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Analytics results → Milestone AI Bridge ``ANALYTICS_EVENT`` JSON.

Translates VAP analytics output into the event document AI Bridge accepts on an
*event topic*, POSTed as ``Content-Type: text/json`` to the sink resolved from
``eventTopics { topicAvailability { rest } }``.

XProtect auto-creates an **Analytics Event** node under Rules and Events for the
registered topic. An operator then binds it to alarm definitions and rules in
Management Client — which is also the documented way to trigger recording, since
AI Bridge exposes no direct recording API.

Two producers feed this module:

* **Live Video Captioning** → :func:`caption_to_event`. Captions are free text
  with no bounding boxes, so they carry no ``involvedObject`` geometry.
* **Object detection** → :func:`detections_to_event`, for discrete occurrences
  (e.g. loitering) as opposed to the per-frame boxes that go to a *metadata*
  topic via :mod:`vms_shim.milestone.onvif_metadata`.

Coordinates differ from the ONVIF metadata path: ``hasOutline`` polygons use
**normalized ``0..1``** vertices, the same convention VAP uses internally, so no
axis inversion is applied here. Getting this wrong is silent — the event still
posts, the overlay is just in the wrong place.

Reference: ``milestonesys/MIP-AIBridge-samples`` →
``apps/golang/connectivitysample/src/application/services/templates/analyticEvent.json``
"""

from __future__ import annotations

from typing import Any

VENDOR_NAME = "Intel Corporation"

# Colour used for object outlines in Smart Client, RGBA 0-255.
DEFAULT_OUTLINE_COLOR = {"red": 0, "green": 163, "blue": 224, "alpha": 255}
DEFAULT_FILL_COLOR = {"red": 0, "green": 163, "blue": 224, "alpha": 48}


def _reference(camera_id: str) -> dict[str, str]:
    """Build the XProtect device reference used by ``fromSource``/``relatedTo``.

    ``uuid`` must be the bare XProtect device GUID — **not** the VAP
    ``milestone:`` prefixed id, and not the composite ``{device}/{stream}``.
    Use :func:`device_id_of` to strip those.
    """
    return {"type": "Reference", "uuid": camera_id}


def device_id_of(camera_id: str) -> str:
    """Reduce any VAP/AI Bridge camera identifier to the bare device GUID.

    Accepts ``"milestone:{device}/{stream}"``, ``"{device}/{stream}"`` or a bare
    device id, so callers can pass whichever form they hold.
    """
    return camera_id.removeprefix("milestone:").split("/", 1)[0]


def _outline_from_bbox(bbox: dict[str, float]) -> dict[str, Any]:
    """Build a closed rectangular ``hasOutline`` polygon from a 0..1 box.

    Expects ``x_min``/``y_min``/``x_max``/``y_max`` in normalized image
    coordinates. Vertices are emitted clockwise from the top-left.
    """
    x0, y0 = bbox["x_min"], bbox["y_min"]
    x1, y1 = bbox["x_max"], bbox["y_max"]
    return {
        "type": "Polygon",
        "closed": True,
        "hasVertices": [
            {"x": x0, "y": y0},
            {"x": x1, "y": y0},
            {"x": x1, "y": y1},
            {"x": x0, "y": y1},
        ],
        "hasLineColor": dict(DEFAULT_OUTLINE_COLOR),
        "hasFillColor": dict(DEFAULT_FILL_COLOR),
    }


def build_event(
    camera_id: str,
    name: str,
    description: str = "",
    event_class: str = "",
    subclass: str = "",
    count: int | None = None,
    tag: str = "",
    objects: list[dict[str, Any]] | None = None,
    snapshot_jpeg_b64: str = "",
    vendor_name: str = VENDOR_NAME,
) -> dict[str, Any]:
    """Assemble an ``ANALYTICS_EVENT`` document.

    Args:
        camera_id: Any camera identifier form; reduced via :func:`device_id_of`.
        name: Short event title shown in XProtect (e.g. ``"Loitering detected"``).
        description: Longer human-readable detail.
        event_class / subclass: Free-form taxonomy. Rules in Management Client
            can filter on these, so keep them stable once deployed.
        count: Optional object count.
        tag: Optional free-form tag.
        objects: Entries for ``involvedObject``, from :func:`build_object`.
        snapshot_jpeg_b64: Base64 JPEG (no data-URI prefix) to attach.
        vendor_name: Reported as the originating vendor.

    Optional members are omitted entirely rather than sent empty, keeping the
    payload small and avoiding empty rows in the XProtect event viewer.
    """
    event: dict[str, Any] = {
        "name": name,
        "fromSource": _reference(device_id_of(camera_id)),
        "fromVendor": {"type": "Vendor", "name": vendor_name},
    }
    if description:
        event["description"] = description
    if event_class:
        event["class"] = event_class
    if subclass:
        event["subclass"] = subclass
    if count is not None:
        event["count"] = count
    if tag:
        event["tag"] = tag
    if objects:
        event["involvedObject"] = objects
    if snapshot_jpeg_b64:
        event["includesSnapshot"] = [{
            "type": "Snapshot",
            "name": "Detection snapshot",
            "imageData": snapshot_jpeg_b64,
        }]
    return event


def build_object(
    name: str,
    object_class: str = "",
    confidence: float | None = None,
    description: str = "",
    bbox: dict[str, float] | None = None,
    trigger: bool = False,
) -> dict[str, Any]:
    """Build one ``involvedObject`` entry.

    Args:
        name: Display name (e.g. ``"Detected car"``).
        object_class: Object class (e.g. ``"Car"``).
        confidence: Detector confidence, ``0..1``.
        description: Optional detail text.
        bbox: Normalized ``0..1`` box with ``x_min``/``y_min``/``x_max``/
            ``y_max``; rendered as a closed ``hasOutline`` polygon.
        trigger: Marks this object as the one that triggered the event.
    """
    obj: dict[str, Any] = {"type": "Object", "name": name}
    if object_class:
        obj["class"] = object_class
    if confidence is not None:
        obj["confidence"] = round(float(confidence), 4)
    if description:
        obj["description"] = description
    if trigger:
        obj["trigger"] = True
    if bbox:
        obj["hasOutline"] = _outline_from_bbox(bbox)
    return obj


def caption_to_event(
    caption: str,
    camera_id: str,
    app_display_name: str = "Live Video Captioning",
    snapshot_jpeg_b64: str = "",
    max_name_length: int = 120,
) -> dict[str, Any]:
    """Translate an LVC caption into an analytics event.

    The caption becomes the event ``name`` so it is legible in the XProtect
    event list, truncated to ``max_name_length`` there while the full text is
    preserved in ``description``.

    Args:
        caption: Caption text from the LVC MQTT ``result`` field.
        camera_id: Any camera identifier form.
        app_display_name: Used as the event ``class`` for rule filtering.
        snapshot_jpeg_b64: Optional base64 JPEG frame.
        max_name_length: Truncation threshold for the event title.
    """
    text = " ".join(str(caption).split())
    name = (
        f"{text[: max_name_length - 1]}…" if len(text) > max_name_length else text
    ) or "Caption"

    return build_event(
        camera_id=camera_id,
        name=name,
        description=text,
        event_class=app_display_name,
        subclass="caption",
        snapshot_jpeg_b64=snapshot_jpeg_b64,
    )


def detections_to_event(
    payload: dict[str, Any],
    camera_id: str,
    event_name: str = "Object detected",
    app_display_name: str = "Object Detection",
    label_class_map: dict[str, str] | None = None,
    snapshot_jpeg_b64: str = "",
) -> dict[str, Any] | None:
    """Translate a DL Streamer metadata payload into a single analytics event.

    Every detection in the payload becomes one ``involvedObject`` with its
    normalized ``0..1`` outline. Returns ``None`` when the payload contains no
    usable detections, so callers can skip the POST entirely rather than raising
    an empty event in XProtect.

    Args:
        payload: Raw DL Streamer MQTT metadata (same shape as
            :func:`vms_shim.milestone.onvif_metadata.normalize_detections`).
        camera_id: Any camera identifier form.
        event_name: Event title.
        app_display_name: Used as the event ``class``.
        label_class_map: Case-insensitive detection label → reported class name.
        snapshot_jpeg_b64: Optional base64 JPEG frame.
    """
    class_map = {k.lower(): v for k, v in (label_class_map or {}).items()}

    objects: list[dict[str, Any]] = []
    for obj in payload.get("objects") or []:
        detection = obj.get("detection") or {}
        raw_box = detection.get("bounding_box") or {}
        label = str(detection.get("label") or obj.get("roi_type") or "object")
        confidence = detection.get("confidence")

        bbox = None
        if all(
            raw_box.get(k) is not None
            for k in ("x_min", "y_min", "x_max", "y_max")
        ):
            bbox = {k: float(raw_box[k]) for k in ("x_min", "y_min", "x_max", "y_max")}

        objects.append(build_object(
            name=f"Detected {label}",
            object_class=class_map.get(label.lower(), label),
            confidence=float(confidence) if confidence is not None else None,
            bbox=bbox,
        ))

    if not objects:
        return None

    return build_event(
        camera_id=camera_id,
        name=event_name,
        description=f"{len(objects)} object(s) detected by {app_display_name}",
        event_class=app_display_name,
        subclass="detection",
        count=len(objects),
        objects=objects,
        snapshot_jpeg_b64=snapshot_jpeg_b64,
    )
