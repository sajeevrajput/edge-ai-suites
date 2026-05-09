"""DLStreamer metadata → Nx analytics object push payload translator.

Converts the inference metadata published by DLStreamer Pipeline Server
to MQTT into the list of Nx object-push dicts expected by
``POST /rest/v4/analytics/engines/{engineId}/deviceAgents/{deviceId}/metadata/object``.

Sample DLS payload shape (see sample_app_metadata.json):
{
  "objects": [
    {
      "detection": {
        "bounding_box": {"x_min": 0.87, "x_max": 0.99, "y_min": 0.16, "y_max": 0.31},
        "confidence": 0.745,
        "label": "car",
        "label_id": 2
      },
      "region_id": 1,
      "roi_type": "car",
      ...
    }
  ],
  "rtp": {"sender_ntp_unix_timestamp_ns": 1777350580751188754},
  "timestamp": 66611537331
}
"""

from __future__ import annotations

import time
import uuid
from typing import Any


_OBJECT_TYPE_PREFIX = "nx.objectDetection"


def translate_dls_metadata(
    payload: dict[str, Any],
    object_type_id: str = _OBJECT_TYPE_PREFIX,
) -> tuple[list[dict[str, Any]], int]:
    """Convert a DLS inference metadata payload to Nx push format.

    Returns a tuple of:
    - list of Nx object dicts (may be empty if no valid detections)
    - timestamp_ms to use for the metadata push

    The timestamp is taken from the RTP ``sender_ntp_unix_timestamp_ns``
    field (converted to milliseconds). If absent, wall-clock time is used.
    """
    rtp = payload.get("rtp") or {}
    ntp_ns = rtp.get("sender_ntp_unix_timestamp_ns", 0)
    if ntp_ns:
        timestamp_ms = ntp_ns // 1_000_000
    else:
        timestamp_ms = int(time.time() * 1000)

    objects: list[dict[str, Any]] = []
    for obj in payload.get("objects", []):
        detection = obj.get("detection") or {}
        bbox = detection.get("bounding_box") or {}

        x_min = bbox.get("x_min")
        y_min = bbox.get("y_min")
        x_max = bbox.get("x_max")
        y_max = bbox.get("y_max")
        if None in (x_min, y_min, x_max, y_max):
            continue

        width = max(0.0, x_max - x_min)
        height = max(0.0, y_max - y_min)
        # Nx bounding box format: "x,y,widthxheight" (all normalized 0–1)
        bounding_box = f"{x_min:.4f},{y_min:.4f},{width:.4f}x{height:.4f}"

        confidence = float(detection.get("confidence", 0.0))
        label = detection.get("label") or obj.get("roi_type") or "unknown"
        region_id = obj.get("region_id")

        # Use region_id as a stable per-object track seed; fall back to random UUID.
        track_id = str(uuid.UUID(int=region_id)) if region_id else str(uuid.uuid4())

        attributes = [
            {"type": "String", "name": "label", "value": label, "confidence": confidence},
        ]

        objects.append(
            {
                "trackId": track_id,
                "typeId": object_type_id,
                "boundingBox": bounding_box,
                "confidence": confidence,
                "attributes": attributes,
            }
        )

    return objects, timestamp_ms
