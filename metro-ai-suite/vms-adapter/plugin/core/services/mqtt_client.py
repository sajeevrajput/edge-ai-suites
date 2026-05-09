"""Generic MQTT result client for the VMS Adapter Plugin.

Connects directly to the MQTT broker and distributes incoming inference
results to per-run ``asyncio.Queue`` instances that the SSE route reads.

Design
------
* Generic — any Core App shim that overrides ``mqtt_topic_prefix()`` gets
  automatic MQTT support with no route changes.
* Wildcard subscription — subscribes to ``{prefix}/#`` so it captures all
  run topics without knowing run IDs in advance.
* Per-run queues — ``subscribe_run(prefix, run_id)`` returns a dedicated
  ``asyncio.Queue``; the SSE generator reads from it and formats SSE events.
* Broadcast queue — ``broadcast_queue(prefix)`` returns a queue that receives
  ALL messages for a prefix (used when no ``run_id`` filter is requested).
* Heartbeat — sends a ``{"type": "status"}`` event every second when no MQTT
  message arrives, matching LVC's own SSE heartbeat behaviour.
* Reconnect — paho-mqtt auto-reconnects and re-subscribes all active prefixes.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

import paho.mqtt.client as mqtt
import structlog

logger = structlog.get_logger(__name__)

# Maximum queue depth before oldest messages are dropped.
_QUEUE_MAX = 500


class MqttResultClient:
    """Generic MQTT subscriber that routes results to per-run async queues."""

    def __init__(self, host: str, port: int = 1883) -> None:
        self._host = host
        self._port = port
        self._client: Optional[mqtt.Client] = None
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # topic_prefix → asyncio.Queue  (broadcast: all runs for that prefix)
        self._broadcast: dict[str, asyncio.Queue] = {}
        # run_id → asyncio.Queue  (per-run: filtered by run_id)
        self._run_queues: dict[str, asyncio.Queue] = {}
        # Internal bridge: paho thread → asyncio event loop
        self._raw_queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        # Subscribed prefixes (so we can re-subscribe after reconnect)
        self._prefixes: set[str] = set()

        self._processor_task: Optional[asyncio.Task] = None

    # ── paho callbacks ────────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self._connected = True
            logger.info("mqtt_connected", host=self._host, port=self._port)
            # Re-subscribe all known prefixes (handles reconnect)
            for prefix in self._prefixes:
                client.subscribe(f"{prefix}/#")
                logger.info("mqtt_subscribed", topic=f"{prefix}/#")
        else:
            self._connected = False
            logger.error("mqtt_connect_failed", rc=rc)

    def _on_disconnect(self, client, userdata, rc, properties=None):
        self._connected = False
        logger.warning("mqtt_disconnected", rc=rc)

    def _on_message(self, client, userdata, msg):
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._raw_queue.put((msg.topic, msg.payload.decode("utf-8"), time.time())),
                self._loop,
            )
        except Exception:
            logger.warning("mqtt_queue_full_dropped", topic=msg.topic)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Connect to the broker and start the message processor."""
        if self._client is not None:
            return
        if not self._host:
            logger.warning("mqtt_no_host_configured")
            return

        self._loop = asyncio.get_event_loop()
        self._client = mqtt.Client(
            client_id=f"vms-adapter-{int(time.time())}",
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

        try:
            self._client.connect_async(self._host, self._port, keepalive=60)
            self._client.loop_start()
            # Wait up to 5 s for initial connection
            for _ in range(50):
                if self._connected:
                    break
                await asyncio.sleep(0.1)
            if not self._connected:
                logger.warning("mqtt_initial_connect_timeout")
        except Exception as exc:
            logger.error("mqtt_connect_error", error=str(exc))
            return

        self._processor_task = asyncio.create_task(self._process_messages())

    async def disconnect(self) -> None:
        """Disconnect and clean up."""
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
            self._processor_task = None

        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            self._connected = False
            logger.info("mqtt_disconnected_cleanly")

    # ── subscription management ───────────────────────────────────────────────

    def subscribe_prefix(self, prefix: str) -> None:
        """Subscribe to all run topics for a Core App prefix (e.g. 'live-video-captioning')."""
        if prefix in self._prefixes:
            return
        self._prefixes.add(prefix)
        if not prefix in self._broadcast:
            self._broadcast[prefix] = asyncio.Queue(maxsize=_QUEUE_MAX)
        if self._client and self._connected:
            self._client.subscribe(f"{prefix}/#")
            logger.info("mqtt_subscribed", topic=f"{prefix}/#")

    def subscribe_run(self, prefix: str, run_id: str) -> asyncio.Queue:
        """Return a per-run queue, creating one if needed.

        The SSE generator should call this when a client connects and
        ``release_run`` when it disconnects.
        """
        self.subscribe_prefix(prefix)
        if run_id not in self._run_queues:
            self._run_queues[run_id] = asyncio.Queue(maxsize=_QUEUE_MAX)
        return self._run_queues[run_id]

    def release_run(self, run_id: str) -> None:
        """Remove the per-run queue (call when SSE client disconnects or run stops)."""
        self._run_queues.pop(run_id, None)

    def broadcast_queue(self, prefix: str) -> asyncio.Queue:
        """Return the broadcast queue for a prefix (all runs)."""
        self.subscribe_prefix(prefix)
        return self._broadcast[prefix]

    # ── message processing ────────────────────────────────────────────────────

    async def _process_messages(self) -> None:
        """Dispatch MQTT messages from the raw queue to per-run and broadcast queues."""
        while True:
            try:
                topic, payload, received_at = await self._raw_queue.get()

                # Parse payload
                try:
                    raw = json.loads(payload)
                except json.JSONDecodeError:
                    raw = {"raw": payload}

                # Unwrap metadata field (LVC format: {"metadata": {...}, "blob": ""})
                if isinstance(raw, dict) and "metadata" in raw:
                    data = raw["metadata"]
                else:
                    data = raw

                # Only forward messages that contain inference results
                if not isinstance(data, dict) or "result" not in data:
                    continue

                # Extract prefix and run_id from topic: "{prefix}/{run_id}"
                parts = topic.rsplit("/", 1)
                if len(parts) != 2:
                    continue
                prefix, run_id = parts[0], parts[1]

                # Build SSE envelope (same format as LVC's own multiplexed stream)
                envelope = {
                    "runId": run_id,
                    "data": data,
                    "received_at": received_at,
                }

                # Put in per-run queue if anyone is listening
                run_q = self._run_queues.get(run_id)
                if run_q is not None:
                    try:
                        run_q.put_nowait(envelope)
                    except asyncio.QueueFull:
                        # Drop oldest message
                        try:
                            run_q.get_nowait()
                            run_q.put_nowait(envelope)
                        except asyncio.QueueEmpty:
                            pass

                # Put in broadcast queue for prefix
                bcast_q = self._broadcast.get(prefix)
                if bcast_q is not None:
                    try:
                        bcast_q.put_nowait(envelope)
                    except asyncio.QueueFull:
                        try:
                            bcast_q.get_nowait()
                            bcast_q.put_nowait(envelope)
                        except asyncio.QueueEmpty:
                            pass

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("mqtt_process_error", error=str(exc))
                await asyncio.sleep(0.1)

    # ── properties ────────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected
