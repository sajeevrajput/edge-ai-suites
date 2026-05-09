"""DLStreamer Pipeline Server REST API client.

Wraps all HTTP calls to the Pipeline Server REST API.
Reference API spec: core_app_shim/dlstreamer/pipeline-server.yaml
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


class ObjectDetectionApiClient:
    """Async HTTP client for the DLStreamer Pipeline Server REST API."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    # ── Pipelines (templates) ─────────────────────────────────────────────────

    async def list_pipelines(self) -> list[dict[str, Any]]:
        """GET /pipelines — list available pipeline templates."""
        client = self._ensure_client()
        try:
            resp = await client.get("/pipelines")
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except httpx.HTTPError as exc:
            logger.error("od_list_pipelines_failed", error=str(exc))
            return []

    async def get_pipeline(self, name: str, version: str) -> dict[str, Any] | None:
        """GET /pipelines/{name}/{version} — get pipeline description."""
        client = self._ensure_client()
        try:
            resp = await client.get(f"/pipelines/{name}/{version}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error("od_get_pipeline_failed", name=name, version=version, error=str(exc))
            return None

    # ── Pipeline instances (runs) ─────────────────────────────────────────────

    async def start_run(
        self, name: str, version: str, payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """POST /pipelines/{name}/{version} — start a new pipeline instance.

        Returns the created instance dict on success, or None on failure.
        """
        client = self._ensure_client()
        try:
            resp = await client.post(f"/pipelines/{name}/{version}", json=payload)
            if not resp.is_success:
                logger.error(
                    "od_start_run_failed",
                    name=name,
                    version=version,
                    status_code=resp.status_code,
                    detail=resp.text[:200],
                )
                return None
            # Pipeline Server returns the instance_id as a plain integer or in a dict
            result = resp.json()
            if isinstance(result, (int, str)):
                return {"instance_id": result, "pipeline": f"{name}/{version}"}
            return result
        except httpx.HTTPError as exc:
            logger.error("od_start_run_error", name=name, version=version, error=str(exc))
            return None

    async def list_runs(self) -> list[dict[str, Any]]:
        """GET /pipelines/status — list all running pipeline instances."""
        client = self._ensure_client()
        try:
            resp = await client.get("/pipelines/status")
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except httpx.HTTPError as exc:
            logger.error("od_list_runs_failed", error=str(exc))
            return []

    async def get_run(
        self, name: str, version: str, instance_id: str | int,
    ) -> dict[str, Any] | None:
        """GET /pipelines/{name}/{version}/{instance_id} — get instance status."""
        client = self._ensure_client()
        try:
            resp = await client.get(f"/pipelines/{name}/{version}/{instance_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error(
                "od_get_run_failed",
                name=name, version=version, instance_id=instance_id, error=str(exc),
            )
            return None

    async def stop_run(
        self, name: str, version: str, instance_id: str | int,
    ) -> bool:
        """DELETE /pipelines/{name}/{version}/{instance_id} — stop a pipeline instance."""
        client = self._ensure_client()
        try:
            resp = await client.delete(f"/pipelines/{name}/{version}/{instance_id}")
            resp.raise_for_status()
            logger.info("od_run_stopped", name=name, version=version, instance_id=instance_id)
            return True
        except httpx.HTTPError as exc:
            logger.error(
                "od_stop_run_failed",
                name=name, version=version, instance_id=instance_id, error=str(exc),
            )
            return False

    # ── Health ────────────────────────────────────────────────────────────────

    async def is_reachable(self) -> bool:
        """Health check — GET /pipelines returning < 500 means the server is up."""
        client = self._ensure_client()
        try:
            resp = await client.get("/pipelines")
            return resp.status_code < 500
        except httpx.HTTPError:
            return False
