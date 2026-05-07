"""Integration tests for /healthz and /readyz."""

from __future__ import annotations

from typing import Any, cast

import httpx
import respx  # type: ignore[import-not-found]
from fastapi.testclient import TestClient

ALERTMANAGER = "http://alertmanager.test"


def test_healthz_always_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_runtime_services_not_attached_to_app_state(client: TestClient) -> None:
    app = cast(Any, client.app)
    assert not hasattr(app.state, "settings")
    assert not hasattr(app.state, "alertmanager_client")
    assert not hasattr(app.state, "mimir_client")
    assert not hasattr(app.state, "aihorde_client")
    assert not hasattr(app.state, "auth_guard")


def test_readyz_ok_when_alertmanager_ready(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{ALERTMANAGER}/-/ready").mock(return_value=httpx.Response(200))
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["upstreams"]["alertmanager"] == "ok"


def test_readyz_503_when_alertmanager_down(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{ALERTMANAGER}/-/ready").mock(return_value=httpx.Response(500))
    response = client.get("/readyz")
    assert response.status_code == 503
