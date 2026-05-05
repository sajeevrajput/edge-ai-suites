"""SQLAlchemy 2 async ORM models for PostgreSQL persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CameraRow(Base):
    __tablename__ = "cameras"

    camera_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    # Legacy columns kept nullable for backward-compat with existing DBs;
    # the plugin no longer reads or writes ingest_mode / watch_path.
    ingest_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    watch_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    stream_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    vendor_meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class MetadataEventRow(Base):
    __tablename__ = "metadata_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    camera_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    labels: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    clip_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingest_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    vendor_meta: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_events_camera_started", "camera_id", started_at.desc()),
    )
