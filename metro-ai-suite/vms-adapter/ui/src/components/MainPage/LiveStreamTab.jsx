/**
 * LiveStreamTab — Live Video Captioning stream viewer.
 *
 * Configuration is done via the App-Specific Config modal.
 * This tab shows the WebRTC WHEP video stream and live AI captions.
 */

import { useEffect, useRef, useState } from 'react';
import { Video, Loader2, Wifi, WifiOff, StopCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import useLvcStream from '@/hooks/useLvcStream';

// ── WHEP helper ───────────────────────────────────────────────────────────────

async function connectWhep(whepUrl, videoEl) {
  const pc = new RTCPeerConnection({
    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
  });

  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.addTransceiver('audio', { direction: 'recvonly' });

  pc.ontrack = (ev) => {
    if (videoEl && ev.streams?.[0]) videoEl.srcObject = ev.streams[0];
  };

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  await new Promise((resolve) => {
    if (pc.iceGatheringState === 'complete') { resolve(); return; }
    pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === 'complete') resolve(); };
    setTimeout(resolve, 3000);
  });

  const resp = await fetch(whepUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/sdp' },
    body: pc.localDescription.sdp,
  });

  if (!resp.ok) throw new Error(`WHEP ${resp.status}: ${await resp.text()}`);
  await pc.setRemoteDescription({ type: 'answer', sdp: await resp.text() });

  return () => pc.close();
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function LiveStreamTab({ lvcRuns = [], onStopLvc }) {
  const [whepConnecting, setWhepConnecting] = useState(false);
  const [whepError,      setWhepError]      = useState('');

  const videoRef   = useRef(null);
  const cleanupRef = useRef(null);

  // Use the first active run
  const activeRun = lvcRuns[0] ?? null;

  const { captions, connected: sseConnected } = useLvcStream(lvcRuns.length > 0);
  const runCaptions = activeRun ? (captions[activeRun.runId] ?? []) : [];

  // Connect WebRTC when run starts
  useEffect(() => {
    const webrtcUrl = activeRun?.webrtcUrl || activeRun?.webrtc_url;
    if (!webrtcUrl || !videoRef.current) return;

    setWhepConnecting(true);
    setWhepError('');

    connectWhep(webrtcUrl, videoRef.current)
      .then((cleanup) => {
        cleanupRef.current = cleanup;
        setWhepConnecting(false);
      })
      .catch((err) => {
        setWhepError(`WebRTC: ${err.message}`);
        setWhepConnecting(false);
      });

    return () => {
      cleanupRef.current?.();
      cleanupRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
    };
  }, [activeRun?.runId]);

  const handleStop = async () => {
    if (!activeRun) return;
    try {
      await onStopLvc(activeRun.runId || activeRun.run_id);
      if (videoRef.current) videoRef.current.srcObject = null;
      cleanupRef.current?.();
      cleanupRef.current = null;
    } catch { /* toast handled in App.jsx */ }
  };

  return (
    <div className="flex flex-col gap-3 max-w-[480px]">
      {/* ── Run info bar ── */}
      {activeRun && (
        <div className="flex items-center justify-between px-3 py-1.5 bg-[#EBF5FF] rounded border border-[#C3DCF5]">
          <div className="flex items-center gap-2 text-[0.72rem]">
            <span className="font-semibold text-[#0E1C47]">Run:</span>
            <span className="font-mono text-[#0071C5] truncate max-w-[120px]">{activeRun.runId}</span>
            <span className="vms-badge vms-badge-green text-[0.62rem]">{activeRun.status ?? 'running'}</span>
            <span className="flex items-center gap-1 text-[#6B7BA4]">
              {sseConnected
                ? <><Wifi size={10} className="text-[#0DBF8C]" /> live</>
                : <><WifiOff size={10} className="text-[#A3B0CC]" /> off</>}
            </span>
          </div>
          <Button
            size="sm"
            variant="destructive"
            className="h-6 text-[0.68rem] px-2"
            onClick={handleStop}
          >
            <StopCircle size={11} className="mr-1" />Stop
          </Button>
        </div>
      )}

      {whepError && <p className="text-[0.72rem] text-red-500 px-1">{whepError}</p>}

      {/* ── Video player ── */}
      <div className="relative bg-black rounded overflow-hidden w-full aspect-video flex items-center justify-center" style={{ maxHeight: '240px' }}>
        {!activeRun && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/40">
            <Video size={32} strokeWidth={1.2} />
            <p className="text-[0.72rem]">Configure and click "Start Analysis" to begin</p>
          </div>
        )}
        {activeRun && whepConnecting && (
          <div className="absolute inset-0 flex items-center justify-center">
            <Loader2 size={24} className="animate-spin text-white/60" />
          </div>
        )}
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          className={`w-full h-full object-contain ${activeRun ? 'opacity-100' : 'opacity-0'}`}
        />
      </div>

      {/* ── Caption ticker ── */}
      {activeRun && (
        <div className="vms-surface p-2 flex flex-col gap-1.5 max-h-[100px] overflow-y-auto">
          <p className="text-[0.62rem] font-bold uppercase tracking-[0.6px] text-[#6B7BA4]">Live Captions</p>
          {runCaptions.length === 0 ? (
            <p className="text-[0.72rem] italic text-[#A3B0CC]">Waiting for captions…</p>
          ) : (
            runCaptions.map((cap, i) => (
              <div key={i} className={`text-[0.75rem] leading-snug px-2 py-0.5 rounded ${
                i === 0 ? 'bg-[#EBF5FF] text-[#0E1C47] font-medium' : 'text-[#4A5C80]'
              }`}>{cap}</div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
