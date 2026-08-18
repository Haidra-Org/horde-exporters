"""CORS must let a browser on another origin call the moderator (POST/PATCH) routes.

Regression: the middleware only allowed GET, so aihorde.net/status could read
status but every incident submission failed at the preflight.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx  # type: ignore[import-not-found]
from fastapi.testclient import TestClient

from ai_horde_service_alerts.app import create_app
from ai_horde_service_alerts.db.session import DatabaseBundle
from ai_horde_service_alerts.settings import HordeAlertsSettings

ORIGIN = "https://aihorde.net"


@pytest.fixture
def cors_client(
    settings: HordeAlertsSettings,
    database_bundle: DatabaseBundle,
    respx_mock: respx.MockRouter,
) -> Iterator[TestClient]:
    cors_settings = settings.model_copy(update={"cors_allow_origins": [ORIGIN]})
    app = create_app(cors_settings, database=database_bundle)
    with TestClient(app) as test_client:
        _ = respx_mock
        yield test_client


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
def test_preflight_allows_moderator_methods(cors_client: TestClient, method: str) -> None:
    response = cors_client.options(
        "/api/v1/internal/incidents",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "apikey, content-type",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == ORIGIN
    allowed = {m.strip() for m in response.headers["access-control-allow-methods"].split(",")}
    assert method in allowed
    allowed_headers = {h.strip().lower() for h in response.headers["access-control-allow-headers"].split(",")}
    assert {"apikey", "content-type"} <= allowed_headers
