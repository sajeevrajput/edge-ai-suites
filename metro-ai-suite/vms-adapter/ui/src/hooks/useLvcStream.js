/**
 * useLvcStream — subscribes to the Live Captioning SSE metadata stream.
 *
 * Returns a `captions` map keyed by runId, where each value is an array of
 * caption strings (most-recent first), plus the raw last envelope object.
 *
 * Usage:
 *   const { captions, connected } = useLvcStream(enabled);
 *   const myCaptions = captions[runId] ?? [];
 */

import { useEffect, useRef, useState } from 'react';

const SSE_URL = '/v1/live-captioning/stream';

export default function useLvcStream(enabled = false) {
  const [captions, setCaptions] = useState({});   // { [runId]: string[] }
  const [connected, setConnected] = useState(false);
  const esRef = useRef(null);

  useEffect(() => {
    if (!enabled) {
      esRef.current?.close();
      esRef.current = null;
      setConnected(false);
      return;
    }

    const es = new EventSource(SSE_URL);
    esRef.current = es;

    es.onopen = () => setConnected(true);

    es.onmessage = (ev) => {
      try {
        const envelope = JSON.parse(ev.data);
        // Skip status heartbeats — only handle caption envelopes
        if (envelope?.type === 'status' || !envelope?.runId) return;

        const data = envelope.data ?? {};
        const text =
          data.text ||
          data.caption ||
          data.result ||
          data.objects?.[0]?.meta?.label ||
          (typeof data === 'string' ? data : null);

        if (text) {
          setCaptions((prev) => {
            const existing = prev[envelope.runId] ?? [];
            return { ...prev, [envelope.runId]: [text, ...existing].slice(0, 20) };
          });
        }
      } catch {
        // non-JSON keep-alive comment — ignore
      }
    };

    es.onerror = () => setConnected(false);

    return () => {
      es.close();
      esRef.current = null;
      setConnected(false);
    };
  }, [enabled]);

  return { captions, connected };
}
