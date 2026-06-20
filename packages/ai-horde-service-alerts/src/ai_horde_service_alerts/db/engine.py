"""Async SQLAlchemy engine + sessionmaker construction."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ai_horde_service_alerts.settings import HordeAlertsSettings


def build_engine(settings: HordeAlertsSettings) -> AsyncEngine:
    """Construct an async engine for the configured ``database_url``.

    SQLite URLs (used in tests) are detected and built without pool sizing,
    which the aiosqlite driver does not support.
    """
    url = settings.database_url
    if url.startswith("sqlite"):
        return create_async_engine(url, echo=settings.database_echo, future=True)
    return create_async_engine(
        url,
        echo=settings.database_echo,
        future=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
    )


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to the engine."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False, class_=AsyncSession)
