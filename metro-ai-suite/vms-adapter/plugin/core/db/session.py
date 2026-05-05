"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db(database_url: str) -> None:
    """Initialize the async engine, session factory, and create tables."""
    global _engine, _session_factory
    _engine = create_async_engine(database_url, echo=False, pool_size=5, max_overflow=5)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    from plugin.core.models.db import Base
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose the engine."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory (must call init_db first)."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _session_factory
