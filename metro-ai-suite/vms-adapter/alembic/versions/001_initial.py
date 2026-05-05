"""001 initial schema :cameras and metadata_events tables.

Revision ID: 001_initial
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cameras",
        sa.Column("camera_id", sa.String(255), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("vendor", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), server_default="unknown"),
        sa.Column("ingest_mode", sa.String(30), server_default="folder_watchdog"),
        sa.Column("watch_path", sa.Text, nullable=True),
        sa.Column("stream_url", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, server_default="false"),
        sa.Column("last_seen_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("vendor_meta", JSONB, server_default="{}"),
    )

    op.create_table(
        "metadata_events",
        sa.Column("event_id", sa.String(255), primary_key=True),
        sa.Column("camera_id", sa.String(255), nullable=False, index=True),
        sa.Column("event_type", sa.String(50), nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("ended_at", sa.DateTime, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("labels", ARRAY(sa.String), server_default="{}"),
        sa.Column("clip_url", sa.Text, nullable=True),
        sa.Column("ingest_mode", sa.String(30), server_default="folder_watchdog"),
        sa.Column("vendor_meta", JSONB, server_default="{}"),
    )

    op.create_index(
        "ix_events_camera_started",
        "metadata_events",
        ["camera_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_events_camera_started", table_name="metadata_events")
    op.drop_table("metadata_events")
    op.drop_table("cameras")
