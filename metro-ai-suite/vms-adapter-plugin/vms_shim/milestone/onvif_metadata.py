# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Detection metadata → ONVIF analytics XML for Milestone AI Bridge.

Translates the normalized inference metadata produced by DL Streamer Pipeline
Server into the two ONVIF payloads AI Bridge accepts on a *metadata topic*:

* :func:`build_frame_xml`            → ``ONVIF_ANALYTICS_FRAME`` (a bare
  ``tt:Frame``; VAP's default, mapped from ``object_detection`` apps)
* :func:`build_metadata_stream_xml`  → ``ONVIF_ANALYTICS`` (a full
  ``tt:MetadataStream`` wrapper)

Both are POSTed as ``Content-Type: text/xml`` to the sink resolved from
``metadataTopics { topicAvailability { rest } }``. XProtect renders the boxes in
Smart Client and indexes them for Metadata Search.

Coordinate systems
------------------
This is the subtle part, and the most likely source of "boxes appear mirrored"
bugs. VAP and DL Streamer use **normalized image coordinates** — ``0..1`` with
the origin at the *top*-left and ``y`` increasing *downwards*. ONVIF uses
``-1..+1`` with the origin at the *centre* and ``y`` increasing **upwards**::

    image (0..1, y down)          ONVIF (-1..+1, y up)
    (0,0) ┌─────────┐             (-1,+1) ┌─────────┐ (+1,+1)
          │         │                     │　 (0,0) │
          └─────────┘ (1,1)       (-1,-1) └─────────┘ (+1,-1)

so the ``y`` axis is **inverted**, not merely rescaled:

* ``left   = 2·x_min − 1``     * ``top    = 1 − 2·y_min``
* ``right  = 2·x_max − 1``     * ``bottom = 1 − 2·y_max``

Verified against Milestone's own ``onvif-frame1.xml`` sample, whose "top left
rectangle" is ``left="-1" right="-0.5" top="1" bottom="0.5"`` — i.e. image-space
``(0,0)..(0.25,0.25)``. See :func:`to_onvif_bbox`.

Serialisation
-------------
Built as strings rather than via ``ElementTree`` so the output matches
Milestone's templates byte-for-byte — notably, the ``ONVIF_ANALYTICS_FRAME``
root carries **no** ``xmlns:tt`` declaration while ``ONVIF_ANALYTICS`` does.
All interpolated values pass through :mod:`xml.sax.saxutils`, so labels
containing ``&`` or ``"`` cannot corrupt the document.

Reference: ``milestonesys/MIP-AIBridge-samples`` →
``apps/golang/connectivitysample/src/application/services/templates/``
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterable
from xml.sax.saxutils import escape, quoteattr

ONVIF_NS = "http://www.onvif.org/ver10/schema"

# Milestone renders these as the box fill/outline in Smart Client. ARGB hex.
DEFAULT_FILL_COLOR = "#3000a3e0"
DEFAULT_LINE_COLOR = "#ff00a3e0"
DEFAULT_LINE_THICKNESS_PX = 2


def rfc3339_nano(timestamp_ms: int | None = None) -> str:
    """Format a UTC timestamp the way AI Bridge expects (Go ``RFC3339Nano``).

    Args:
        timestamp_ms: Unix epoch milliseconds; current time when ``None``.
    """
    ms = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    # Go's RFC3339Nano trims trailing zeros; Python pads to 6 digits. Both parse.
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def to_onvif_bbox(
    x_min: float, y_min: float, x_max: float, y_max: float,
) -> dict[str, float]:
    """Convert a normalized ``0..1`` image box to ONVIF ``-1..+1`` coordinates.

    Inputs are clamped to ``0..1`` first: DL Streamer occasionally emits boxes
    fractionally outside the frame, and XProtect rejects out-of-range values.
    The ``y`` axis is inverted — see the module docstring.

    Returns a dict with ``left``, ``top``, ``right``, ``bottom``.
    """
    x0, x1 = sorted((_clamp01(x_min), _clamp01(x_max)))
    y0, y1 = sorted((_clamp01(y_min), _clamp01(y_max)))
    return {
        "left": 2.0 * x0 - 1.0,
        "right": 2.0 * x1 - 1.0,
        "top": 1.0 - 2.0 * y0,
        "bottom": 1.0 - 2.0 * y1,
    }


def _attr(name: str, value: Any) -> str:
    """Render one XML attribute with proper quoting/escaping."""
    return f"{name}={quoteattr(str(value))}"


def _num(value: float) -> str:
    """Render a coordinate, trimming float noise (e.g. 0.30000000000000004)."""
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _object_xml(obj: dict[str, Any], indent: str = "    ") -> str:
    """Render a single ``tt:Object`` element.

    ``obj`` is a normalized detection dict as produced by
    :func:`normalize_detections`.
    """
    box = obj["bbox"]
    pad = indent
    lines = [
        f"{pad}<tt:Object {_attr('ObjectId', obj['object_id'])}>",
        f"{pad}    <tt:Appearance>",
    ]

    label = obj.get("label")
    if label:
        lines += [
            f"{pad}        <tt:Class>",
            f"{pad}            <tt:ClassCandidate>",
            f"{pad}                <tt:Type>{escape(str(label))}</tt:Type>",
            f"{pad}                <tt:Likelihood>"
            f"{_num(obj.get('confidence', 0.0))}</tt:Likelihood>",
            f"{pad}            </tt:ClassCandidate>",
            f"{pad}        </tt:Class>",
        ]

    lines += [
        f"{pad}        <tt:Shape>",
        f"{pad}            <tt:BoundingBox "
        f"{_attr('left', _num(box['left']))} "
        f"{_attr('top', _num(box['top']))} "
        f"{_attr('right', _num(box['right']))} "
        f"{_attr('bottom', _num(box['bottom']))}/>",
        f"{pad}            <tt:Extension>",
        f"{pad}                <BoundingBoxAppearance>",
        f"{pad}                    <Fill {_attr('color', obj.get('fill_color', DEFAULT_FILL_COLOR))} />",
        f"{pad}                    <Line {_attr('color', obj.get('line_color', DEFAULT_LINE_COLOR))} "
        f"{_attr('displayedThicknessInPixels', DEFAULT_LINE_THICKNESS_PX)} />",
        f"{pad}                </BoundingBoxAppearance>",
        f"{pad}            </tt:Extension>",
        f"{pad}        </tt:Shape>",
    ]

    description = obj.get("description")
    if description:
        # Anchor the caption just above the box, clamped inside the frame.
        text_y = min(1.0, box["top"] + 0.04)
        lines += [
            f"{pad}        <tt:Extension>",
            f"{pad}            <Description "
            f"{_attr('x', _num(box['left']))} {_attr('y', _num(text_y))} "
            f"{_attr('size', '0.05')} {_attr('bold', 'true')} "
            f"{_attr('italic', 'false')} {_attr('fontFamily', 'Helvetica')} "
            f"{_attr('color', obj.get('line_color', DEFAULT_LINE_COLOR))}>"
            f"{escape(str(description))}</Description>",
            f"{pad}        </tt:Extension>",
        ]

    lines += [
        f"{pad}    </tt:Appearance>",
        f"{pad}</tt:Object>",
    ]
    return "\n".join(lines)


def build_frame_xml(
    objects: Iterable[dict[str, Any]],
    source_stream_id: str,
    timestamp_ms: int | None = None,
) -> str:
    """Build an ``ONVIF_ANALYTICS_FRAME`` payload (bare ``tt:Frame``).

    Args:
        objects: Normalized detections from :func:`normalize_detections`.
        source_stream_id: AI Bridge composite ``"{deviceId}/{streamId}"``.
        timestamp_ms: Frame wall-clock time in epoch ms; now when ``None``.

    An empty ``objects`` iterable produces a valid empty frame, which is how a
    detector signals "nothing in view" and clears stale boxes in Smart Client.
    """
    body = "\n".join(_object_xml(o) for o in objects)
    head = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<tt:Frame {_attr('UtcTime', rfc3339_nano(timestamp_ms))} "
        f"{_attr('SourceStreamID', source_stream_id)}>"
    )
    return f"{head}\n{body}\n</tt:Frame>" if body else f"{head}\n</tt:Frame>"


def build_metadata_stream_xml(
    objects: Iterable[dict[str, Any]],
    source_stream_id: str,
    timestamp_ms: int | None = None,
) -> str:
    """Build an ``ONVIF_ANALYTICS`` payload (full ``tt:MetadataStream``).

    Same content as :func:`build_frame_xml`, wrapped in the
    ``MetadataStream``/``VideoAnalytics`` envelope and carrying the ``tt``
    namespace declaration.
    """
    body = "\n".join(_object_xml(o, indent="            ") for o in objects)
    frame_open = (
        f"        <tt:Frame {_attr('UtcTime', rfc3339_nano(timestamp_ms))} "
        f"{_attr('SourceStreamID', source_stream_id)}>"
    )
    inner = f"{frame_open}\n{body}\n        </tt:Frame>" if body else (
        f"{frame_open}\n        </tt:Frame>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<tt:MetadataStream xmlns:tt="{ONVIF_NS}">\n'
        "    <tt:VideoAnalytics>\n"
        f"{inner}\n"
        "    </tt:VideoAnalytics>\n"
        "</tt:MetadataStream>"
    )


def normalize_detections(
    payload: dict[str, Any],
    label_class_map: dict[str, str] | None = None,
    show_labels: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """Convert a DL Streamer metadata payload into renderable detection dicts.

    Mirrors ``analytics_app_shim.object_detection.translator.translate_dls_metadata``
    (which targets Nx) but emits ONVIF-space boxes for Milestone.

    Args:
        payload: Raw DL Streamer MQTT metadata. Expected shape::

            {"objects": [{"detection": {"bounding_box": {"x_min":…,"x_max":…,
                                                         "y_min":…,"y_max":…},
                                        "confidence":…, "label":…},
                          "region_id": 1}],
             "rtp": {"sender_ntp_unix_timestamp_ns": …}}

        label_class_map: Optional case-insensitive detection label → ONVIF class
            name (e.g. ``{"car": "Vehicle"}``). Unmapped labels pass through
            unchanged.
        show_labels: Render the label/confidence as on-screen text.

    Returns ``(detections, timestamp_ms)``. Detections missing any bounding-box
    edge are skipped rather than emitted with partial geometry.

    The timestamp is taken from ``rtp.sender_ntp_unix_timestamp_ns`` when
    present so boxes line up with the video frame; otherwise wall-clock now.
    """
    class_map = {k.lower(): v for k, v in (label_class_map or {}).items()}

    rtp = payload.get("rtp") or {}
    ntp_ns = rtp.get("sender_ntp_unix_timestamp_ns", 0)
    timestamp_ms = ntp_ns // 1_000_000 if ntp_ns else int(time.time() * 1000)

    detections: list[dict[str, Any]] = []
    for index, obj in enumerate(payload.get("objects") or [], start=1):
        detection = obj.get("detection") or {}
        bbox = detection.get("bounding_box") or {}
        edges = (
            bbox.get("x_min"), bbox.get("y_min"),
            bbox.get("x_max"), bbox.get("y_max"),
        )
        if any(edge is None for edge in edges):
            continue

        label = detection.get("label") or obj.get("roi_type") or ""
        confidence = float(detection.get("confidence", 0.0) or 0.0)
        onvif_class = class_map.get(str(label).lower(), label)

        description = ""
        if show_labels and label:
            description = (
                f"{label} {confidence:.0%}" if confidence else str(label)
            )

        detections.append({
            # ONVIF ObjectId must be an integer; region_id is a stable per-track
            # id when the pipeline tracks, else fall back to frame ordinal.
            "object_id": int(obj.get("region_id") or index),
            "label": onvif_class,
            "confidence": confidence,
            "description": description,
            "bbox": to_onvif_bbox(*(float(e) for e in edges)),
        })

    return detections, timestamp_ms


def translate_dls_to_onvif(
    payload: dict[str, Any],
    source_stream_id: str,
    metadata_format: str = "ONVIF_ANALYTICS_FRAME",
    label_class_map: dict[str, str] | None = None,
    timestamp_offset_ms: int = 0,
) -> str:
    """One-shot DL Streamer metadata → ONVIF XML for the configured format.

    Args:
        payload: Raw DL Streamer MQTT metadata dict.
        source_stream_id: AI Bridge composite ``"{deviceId}/{streamId}"``.
        metadata_format: ``ONVIF_ANALYTICS_FRAME`` or ``ONVIF_ANALYTICS``;
            must match what was registered for the topic.
        label_class_map: Detection label → ONVIF class name.
        timestamp_offset_ms: Added to the frame timestamp. Use a negative value
            to compensate for inference latency so boxes align with the video.

    Raises:
        ValueError: if ``metadata_format`` is not an ONVIF format.
    """
    detections, timestamp_ms = normalize_detections(payload, label_class_map)
    timestamp_ms += timestamp_offset_ms

    if metadata_format == "ONVIF_ANALYTICS_FRAME":
        return build_frame_xml(detections, source_stream_id, timestamp_ms)
    if metadata_format == "ONVIF_ANALYTICS":
        return build_metadata_stream_xml(detections, source_stream_id, timestamp_ms)
    raise ValueError(
        f"metadata_format '{metadata_format}' is not an ONVIF format; "
        "expected ONVIF_ANALYTICS_FRAME or ONVIF_ANALYTICS",
    )
