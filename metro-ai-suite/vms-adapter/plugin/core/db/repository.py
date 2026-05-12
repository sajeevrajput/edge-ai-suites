# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CRUD repository for cameras and metadata events."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from plugin.core.models.db import AnalyticsSessionRow, CameraRow, MetadataEventRow, NxAnalyticsIntegrationRow
from plugin.core.models.domain import AnalyticsSession, AnalyticsSessionCreate, Camera, MetadataEvent, NxAnalyticsIntegration


async def upsert_camera(session: AsyncSession, camera: Camera) -> None:
    base = pg_insert(CameraRow).values(
        camera_id=camera.camera_id,
        name=camera.name,
        vendor=camera.vendor,
        status=camera.status,
        stream_url=camera.stream_url,
        enabled=camera.enabled,
        last_seen_at=camera.last_seen_at,
        vendor_meta=camera.vendor_meta,
    )
    stmt = base.on_conflict_do_update(
        index_elements=["camera_id"],
        set_={
            "name": base.excluded.name,
            "status": base.excluded.status,
            "stream_url": base.excluded.stream_url,
            "last_seen_at": base.excluded.last_seen_at,
            "vendor_meta": base.excluded.vendor_meta,
        },
    )
    await session.execute(stmt)
    await session.commit()


async def get_all_cameras(session: AsyncSession) -> list[Camera]:
    result = await session.execute(select(CameraRow))
    return [_row_to_camera(r) for r in result.scalars().all()]


async def get_camera(session: AsyncSession, camera_id: str) -> Camera | None:
    result = await session.execute(
        select(CameraRow).where(CameraRow.camera_id == camera_id)
    )
    row = result.scalar_one_or_none()
    return _row_to_camera(row) if row else None


async def update_camera_enabled(
    session: AsyncSession, camera_ids: list[str], enabled: bool,
) -> tuple[list[str], list[str]]:
    existing = await session.execute(
        select(CameraRow.camera_id).where(CameraRow.camera_id.in_(camera_ids))
    )
    existing_ids = {r[0] for r in existing.all()}
    updated = [cid for cid in camera_ids if cid in existing_ids]
    not_found = [cid for cid in camera_ids if cid not in existing_ids]
    if updated:
        await session.execute(
            update(CameraRow).where(CameraRow.camera_id.in_(updated)).values(enabled=enabled)
        )
        await session.commit()
    return updated, not_found


async def insert_event(session: AsyncSession, event: MetadataEvent) -> None:
    stmt = pg_insert(MetadataEventRow).values(
        event_id=event.event_id,
        camera_id=event.camera_id,
        event_type=event.event_type,
        started_at=event.started_at,
        ended_at=event.ended_at,
        confidence=event.confidence,
        labels=event.labels,
        clip_url=event.clip_url,
        vendor_meta=event.vendor_meta,
    ).on_conflict_do_nothing(index_elements=["event_id"])
    await session.execute(stmt)
    await session.commit()


async def get_event(session: AsyncSession, event_id: str) -> MetadataEvent | None:
    result = await session.execute(
        select(MetadataEventRow).where(MetadataEventRow.event_id == event_id)
    )
    row = result.scalar_one_or_none()
    return _row_to_event(row) if row else None


async def get_events_paginated(
    session: AsyncSession,
    camera_id: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MetadataEvent]:
    stmt = select(MetadataEventRow).order_by(MetadataEventRow.started_at.desc())
    if camera_id:
        stmt = stmt.where(MetadataEventRow.camera_id == camera_id)
    if from_dt:
        stmt = stmt.where(MetadataEventRow.started_at >= from_dt)
    if to_dt:
        stmt = stmt.where(MetadataEventRow.started_at <= to_dt)
    result = await session.execute(stmt.limit(limit).offset(offset))
    return [_row_to_event(r) for r in result.scalars().all()]


def _row_to_camera(row: CameraRow) -> Camera:
    return Camera(
        camera_id=row.camera_id,
        name=row.name,
        vendor=row.vendor,
        status=row.status,
        stream_url=row.stream_url,
        enabled=row.enabled,
        last_seen_at=row.last_seen_at,
        vendor_meta=row.vendor_meta or {},
    )


def _row_to_event(row: MetadataEventRow) -> MetadataEvent:
    return MetadataEvent(
        event_id=row.event_id,
        camera_id=row.camera_id,
        event_type=row.event_type,
        started_at=row.started_at,
        ended_at=row.ended_at,
        confidence=row.confidence,
        labels=row.labels or [],
        clip_url=row.clip_url,
        vendor_meta=row.vendor_meta or {},
    )


# ── Analytics session CRUD ───────────────────────────────────────────────────

async def create_session(
    session: AsyncSession, data: AnalyticsSessionCreate,
) -> AnalyticsSession:
    row = AnalyticsSessionRow(
        session_id=str(uuid.uuid4()),
        camera_id=data.camera_id,
        core_app_id=data.core_app_id,
        app_instance_id=data.app_instance_id,
        status="active",
        launch_payload=data.launch_payload,
        app_state=data.app_state,
        started_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _row_to_session(row)


async def get_session(
    session: AsyncSession, session_id: str,
) -> AnalyticsSession | None:
    result = await session.execute(
        select(AnalyticsSessionRow).where(AnalyticsSessionRow.session_id == session_id)
    )
    row = result.scalar_one_or_none()
    return _row_to_session(row) if row else None


async def get_session_by_instance_id(
    session: AsyncSession, app_instance_id: str,
) -> AnalyticsSession | None:
    result = await session.execute(
        select(AnalyticsSessionRow).where(
            AnalyticsSessionRow.app_instance_id == app_instance_id
        )
    )
    row = result.scalar_one_or_none()
    return _row_to_session(row) if row else None


async def list_sessions(
    session: AsyncSession,
    camera_id: str | None = None,
    core_app_id: str | None = None,
    status: str | None = None,
) -> list[AnalyticsSession]:
    stmt = select(AnalyticsSessionRow).order_by(AnalyticsSessionRow.started_at.desc())
    if camera_id:
        stmt = stmt.where(AnalyticsSessionRow.camera_id == camera_id)
    if core_app_id:
        stmt = stmt.where(AnalyticsSessionRow.core_app_id == core_app_id)
    if status:
        stmt = stmt.where(AnalyticsSessionRow.status == status)
    result = await session.execute(stmt)
    return [_row_to_session(r) for r in result.scalars().all()]


async def stop_session(
    session: AsyncSession, session_id: str,
) -> bool:
    """Mark a session as stopped. Returns False if session_id not found."""
    result = await session.execute(
        update(AnalyticsSessionRow)
        .where(AnalyticsSessionRow.session_id == session_id)
        .values(status="stopped", stopped_at=datetime.now(timezone.utc))
        .returning(AnalyticsSessionRow.session_id)
    )
    await session.commit()
    return result.scalar_one_or_none() is not None


def _row_to_session(row: AnalyticsSessionRow) -> AnalyticsSession:
    return AnalyticsSession(
        session_id=row.session_id,
        camera_id=row.camera_id,
        core_app_id=row.core_app_id,
        app_instance_id=row.app_instance_id,
        status=row.status,
        launch_payload=row.launch_payload or {},
        app_state=row.app_state or {},
        started_at=row.started_at,
        stopped_at=row.stopped_at,
    )


# ── Nx Analytics Integration CRUD ───────────────────────────────────────────

async def upsert_nx_integration(
    session: AsyncSession,
    vms_name: str,
    core_app_id: str,
    integration_manifest: dict,
    engine_manifest: dict,
    device_agent_manifest: dict | None,
    nx_username: str | None,
    nx_password: str | None,
    nx_request_id: str | None,
    status: str,
) -> NxAnalyticsIntegration:
    """Insert or update an Nx analytics integration record keyed by (vms_name, core_app_id)."""
    registered_at = datetime.now(timezone.utc) if status == "approved" else None
    base = pg_insert(NxAnalyticsIntegrationRow).values(
        id=str(uuid.uuid4()),
        vms_name=vms_name,
        core_app_id=core_app_id,
        integration_manifest=integration_manifest,
        engine_manifest=engine_manifest,
        device_agent_manifest=device_agent_manifest,
        nx_username=nx_username,
        nx_password=nx_password,
        nx_request_id=nx_request_id,
        status=status,
        registered_at=registered_at,
    )
    stmt = base.on_conflict_do_update(
        index_elements=["vms_name", "core_app_id"],
        set_={
            "integration_manifest": base.excluded.integration_manifest,
            "engine_manifest": base.excluded.engine_manifest,
            "device_agent_manifest": base.excluded.device_agent_manifest,
            "nx_username": base.excluded.nx_username,
            "nx_password": base.excluded.nx_password,
            "nx_request_id": base.excluded.nx_request_id,
            "status": base.excluded.status,
            "registered_at": base.excluded.registered_at,
        },
    ).returning(NxAnalyticsIntegrationRow)
    result = await session.execute(stmt)
    await session.commit()
    row = result.scalar_one()
    return _row_to_nx_integration(row)


async def get_nx_integration(
    session: AsyncSession, vms_name: str, core_app_id: str,
) -> NxAnalyticsIntegration | None:
    result = await session.execute(
        select(NxAnalyticsIntegrationRow).where(
            NxAnalyticsIntegrationRow.vms_name == vms_name,
            NxAnalyticsIntegrationRow.core_app_id == core_app_id,
        )
    )
    row = result.scalar_one_or_none()
    return _row_to_nx_integration(row) if row else None


def _row_to_nx_integration(row: NxAnalyticsIntegrationRow) -> NxAnalyticsIntegration:
    return NxAnalyticsIntegration(
        id=row.id,
        vms_name=row.vms_name,
        core_app_id=row.core_app_id,
        integration_manifest=row.integration_manifest or {},
        engine_manifest=row.engine_manifest or {},
        device_agent_manifest=row.device_agent_manifest,
        nx_username=row.nx_username,
        nx_password=row.nx_password,
        nx_request_id=row.nx_request_id,
        status=row.status,
        registered_at=row.registered_at,
    )
