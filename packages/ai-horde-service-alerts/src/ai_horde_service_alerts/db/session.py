"""Session bundle exposed for FastAPI dependency injection and background tasks."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ai_horde_service_alerts.db.engine import build_engine, build_sessionmaker
from ai_horde_service_alerts.settings import HordeAlertsSettings


@dataclass(frozen=True, slots=True)
class DatabaseBundle:
    """Holds the per-app engine and sessionmaker."""

    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a session inside an async context manager (commits on success, rolls back on error)."""
        async with self.sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        """Dispose the engine and close all pooled connections."""
        await self.engine.dispose()


def build_database_bundle(settings: HordeAlertsSettings) -> DatabaseBundle:
    """Construct a :class:`DatabaseBundle` from settings."""
    engine = build_engine(settings)
    return DatabaseBundle(engine=engine, sessionmaker=build_sessionmaker(engine))
