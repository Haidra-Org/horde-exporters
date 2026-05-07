"""Shared test fixtures for ai-horde-service-alerts."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx  # type: ignore[import-not-found]
from fastapi.testclient import TestClient
from pydantic import HttpUrl

from ai_horde_service_alerts.app import create_app
from ai_horde_service_alerts.settings import HordeAlertsSettings

MODERATOR_KEY = "moderator-test-key"
NON_MODERATOR_KEY = "regular-user-key"
UNKNOWN_KEY = "unknown-key"


@pytest.fixture
def settings() -> HordeAlertsSettings:
    return HordeAlertsSettings(
        alertmanager_base_url=HttpUrl("http://alertmanager.test"),
        mimir_base_url=HttpUrl("http://mimir.test"),
        aihorde_base_url=HttpUrl("http://aihorde.test/api/"),
        aihorde_client_agent="ai-horde-service-alerts-tests:0.1.0:test",
        moderator_cache_ttl_seconds=60,
        moderator_cache_negative_ttl_seconds=15,
        moderator_cache_max_entries=64,
        mimir_curated_queries={},
        cors_allow_origins=[],
        request_timeout_seconds=2.0,
        enable_internal_swagger_docs=True,
    )


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
    respx_mock: respx.MockRouter,
) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        # tag fixture refs so type-checkers / linters do not flag them as unused
        _ = respx_mock
        yield test_client


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
