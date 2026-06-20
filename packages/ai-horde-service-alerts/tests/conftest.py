"""Shared test fixtures for ai-horde-service-alerts.

The DB-backed integration tests use a SQLite database written to a temporary
file (so it is visible across the engine's pooled connections, including
those opened from the TestClient's anyio event loop). Schema is created
from the SQLAlchemy metadata; Alembic migrations are not run in tests.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx  # type: ignore[import-not-found]
from fastapi.testclient import TestClient
from pydantic import HttpUrl, SecretStr
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_horde_service_alerts.app import create_app
from ai_horde_service_alerts.db.base import Base
from ai_horde_service_alerts.db.session import DatabaseBundle
from ai_horde_service_alerts.settings import HordeAlertsSettings

MODERATOR_KEY = "moderator-test-key"
NON_MODERATOR_KEY = "regular-user-key"
UNKNOWN_KEY = "unknown-key"
PROBER_SECRET = "prober-test-secret"


@pytest.fixture
def sqlite_db_path() -> Iterator[str]:
    """Yield a temp sqlite file path; remove it after the test."""
    fd, path = tempfile.mkstemp(suffix=".sqlite", prefix="horde-status-tests-")
    os.close(fd)
    try:
        yield path
    finally:
        with suppress(FileNotFoundError):
            os.unlink(path)


@pytest.fixture
def settings(sqlite_db_path: str) -> HordeAlertsSettings:
    """Test settings: file-backed sqlite, background tasks off, prober secret set."""
    return HordeAlertsSettings(
        alertmanager_base_url=HttpUrl("http://alertmanager.test"),
        mimir_base_url=HttpUrl("http://mimir.test"),
        aihorde_base_url=HttpUrl("http://aihorde.test/api/"),
        aihorde_client_agent="ai-horde-service-alerts-tests:0.1.0:test",
        moderator_cache_ttl_seconds=60,
        moderator_cache_negative_ttl_seconds=15,
        moderator_cache_max_entries=64,
        cors_allow_origins=[],
        request_timeout_seconds=2.0,
        enable_internal_swagger_docs=True,
        database_url=f"sqlite+aiosqlite:///{sqlite_db_path}",
        enable_db=True,
        enable_background_tasks=False,
        prober_shared_secret=SecretStr(PROBER_SECRET),
        backfill_on_startup=False,
    )


@pytest.fixture
def database_bundle(
    settings: HordeAlertsSettings,
    sqlite_db_path: str,
) -> Iterator[DatabaseBundle]:
    """Build an async engine for the test DB; create the schema synchronously first."""
    sync_engine = create_engine(f"sqlite:///{sqlite_db_path}", future=True)
    try:
        Base.metadata.create_all(sync_engine)
    finally:
        sync_engine.dispose()

    engine = create_async_engine(settings.database_url, future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    bundle = DatabaseBundle(engine=engine, sessionmaker=sessionmaker)
    yield bundle
    # NOTE: the async engine's dispose() is awaited by the FastAPI lifespan
    # when the TestClient owns the bundle. For non-client-using tests, the
    # process exit will clean up file handles.


@pytest.fixture
def respx_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        _seed_aihorde(router)
        yield router


def _seed_aihorde(router: respx.MockRouter) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        api_key = request.headers.get("apikey", "")
        if api_key == MODERATOR_KEY:
            return httpx.Response(
                200,
                json={"id": 1, "username": "modtest#1", "moderator": True},
            )
        if api_key == NON_MODERATOR_KEY:
            return httpx.Response(
                200,
                json={"id": 2, "username": "user#2", "moderator": False},
            )
        return httpx.Response(404, json={"message": "User not found"})

    router.get("http://aihorde.test/api/v2/find_user").mock(side_effect=_handler)


@pytest.fixture
def client(
    settings: HordeAlertsSettings,
    database_bundle: DatabaseBundle,
    respx_mock: respx.MockRouter,
) -> Iterator[TestClient]:
    """FastAPI TestClient bound to the test database. Lifespan runs (seeds components)."""
    app = create_app(settings, database=database_bundle)
    with TestClient(app) as test_client:
        _ = respx_mock
        yield test_client


@pytest_asyncio.fixture
async def db_session(database_bundle: DatabaseBundle) -> AsyncIterator[AsyncSession]:
    """Yield an :class:`AsyncSession` bound to the test database."""
    async with database_bundle.session() as session:
        yield session


@pytest.fixture
def alertmanager_alert() -> dict[str, Any]:
    return {
        "fingerprint": "abc123",
        "startsAt": "2025-01-01T00:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "updatedAt": "2025-01-01T00:01:00Z",
        "status": {"state": "active", "silencedBy": [], "inhibitedBy": []},
        "labels": {
            "alertname": "DiskFillingUp",
            "severity": "warning",
            "component": "storage",
            "service": "ai-horde",
            "instance": "10.0.0.5:9100",
            "pod": "horde-db-0",
        },
        "annotations": {
            "summary": "Disk usage high",
            "description": "Detailed runbook info that must NOT leak.",
            "runbook_url": "https://internal.example/runbook",
        },
        "generatorURL": "https://internal.example/graph",
    }
