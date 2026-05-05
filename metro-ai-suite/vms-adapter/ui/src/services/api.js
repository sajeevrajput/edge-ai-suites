/**
 * VMS Plugin Service API client.
 *
 * All endpoints proxy through Vite dev-server /v1 → http://localhost:8082/v1.
 * In production set VITE_API_BASE env var; the proxy handles routing.
 */

const BASE = '/v1';

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

/** Backend Camera model uses `name`; UI uses `camera_name`. */
function normaliseCamera(cam) {
  return {
    ...cam,
    camera_name: cam.name ?? cam.camera_name ?? cam.camera_id,
  };
}

// ── Health ────────────────────────────────────────────────────────────────────

/** GET /v1/ready — readiness probe. */
export async function getReady() {
  return request('/ready');
}

// ── Cameras ───────────────────────────────────────────────────────────────────

/** GET /v1/cameras — list all cameras stored in DB. */
export async function listCameras() {
  const data = await request('/cameras');
  return data.map(normaliseCamera);
}

/** POST /v1/cameras/discover — active scan across all NVRs (up to 30 s). */
export async function discoverCameras() {
  const data = await request('/cameras/discover', { method: 'POST' });
  return data.map(normaliseCamera);
}

/** POST /v1/cameras/enable — enable or disable cameras by ID. */
export async function setCameraEnabled(cameraIds, enabled) {
  return request('/cameras/enable', {
    method: 'POST',
    body: JSON.stringify({ camera_ids: cameraIds, enabled }),
  });
}

// ── Live Video Captioning ─────────────────────────────────────────────────────

/** GET /v1/live-captioning/pipelines — list available LVC pipelines (CPU/GPU). */
export async function getLvcPipelines() {
  return request('/live-captioning/pipelines');
}

/** GET /v1/live-captioning/models — list available VLM models. */
export async function getLvcModels() {
  return request('/live-captioning/models');
}

/** GET /v1/live-captioning/runs — list active LVC runs (each with peerId for WHEP). */
export async function listLvcRuns() {
  const data = await request('/live-captioning/runs');
  // Enrich each run with relative WHEP URL the UI nginx proxies to MediaMTX.
  return (Array.isArray(data) ? data : []).map((r) => ({
    ...r,
    webrtcUrl: r.webrtcUrl || (r.peerId ? `/whep/${r.peerId}/whep` : ''),
  }));
}

/** DELETE /v1/live-captioning/runs/:runId — stop a run. */
export async function stopLvcRun(runId) {
  return request(`/live-captioning/runs/${encodeURIComponent(runId)}`, { method: 'DELETE' });
}

// ── Core Apps (dynamic discovery) ────────────────────────────────────────────

/**
 * GET /v1/core-apps/discover — list all registered Core Apps with their
 * Pydantic JSON Schemas and live availability.
 */
export async function discoverCoreApps() {
  const data = await request('/core-apps/discover');
  return Array.isArray(data) ? data : [];
}

/**
 * POST /v1/core-apps/:appId/start — validate the payload via the backend's
 * Pydantic model and trigger the analytics run. Throws an error whose
 * `.fieldErrors` is an array of `{loc, msg, type}` on 422 responses.
 */
export async function startCoreApp(appId, payload) {
  const res = await fetch(`${BASE}/core-apps/${encodeURIComponent(appId)}/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload ?? {}),
  });
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch { detail = res.statusText; }
    const err = new Error(`API ${res.status}: ${typeof detail === 'string' ? detail : 'Validation failed'}`);
    err.status = res.status;
    err.fieldErrors = Array.isArray(detail) ? detail : [];
    throw err;
  }
  return res.json();
}
