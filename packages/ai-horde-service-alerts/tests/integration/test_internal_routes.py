"""Integration tests for internal (moderator-gated) routes."""

from __future__ import annotations

import httpx
import respx  # type: ignore[import-not-found]
from fastapi.testclient import TestClient

MODERATOR_KEY = "moderator-test-key"
NON_MODERATOR_KEY = "regular-user-key"
UNKNOWN_KEY = "unknown-key"

ALERTMANAGER = "http://alertmanager.test"
MIMIR = "http://mimir.test"


def test_internal_alerts_requires_apikey(client: TestClient) -> None:
    response = client.get("/api/v1/internal/alerts")
    assert response.status_code == 401


def test_internal_routes_advertise_apikey_security_scheme(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200

    document = response.json()
    assert document["components"]["securitySchemes"]["AiHordeApiKey"] == {
        "type": "apiKey",
        "in": "header",
        "name": "apikey",
        "description": "AI Horde API key used to authorize moderator-only endpoints.",
    }
    assert document["paths"]["/api/v1/internal/alerts"]["get"]["security"] == [{"AiHordeApiKey": []}]


def test_internal_alerts_rejects_non_moderator(client: TestClient) -> None:
    response = client.get(
        "/api/v1/internal/alerts",
        headers={"apikey": NON_MODERATOR_KEY},
    )
    assert response.status_code == 403


def test_internal_alerts_rejects_unknown_user(client: TestClient) -> None:
    response = client.get(
        "/api/v1/internal/alerts",
        headers={"apikey": UNKNOWN_KEY},
    )
    assert response.status_code == 403


def test_internal_alerts_returns_raw_payload_for_moderator(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    raw = [
        {
            "fingerprint": "abc",
            "startsAt": "2025-01-01T00:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "updatedAt": "2025-01-01T00:01:00Z",
            "status": {"state": "active"},
            "labels": {"alertname": "X", "severity": "warning", "instance": "10.0.0.5:9100"},
            "annotations": {"description": "internal", "summary": "s"},
        },
    ]
    respx_mock.get(f"{ALERTMANAGER}/api/v2/alerts").mock(
        return_value=httpx.Response(200, json=raw),
    )
    response = client.get(
        "/api/v1/internal/alerts",
        headers={"apikey": MODERATOR_KEY},
    )
    assert response.status_code == 200
    body = response.text
    # Internal route should preserve full raw fields including "instance" + "description".
    assert "10.0.0.5:9100" in body
    assert "internal" in body


def test_internal_metrics_instant_queries_mimir(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{MIMIR}/prometheus/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"job": "ai-horde"},
                            "value": [1.0, "1"],
                        },
                    ],
                },
            },
        ),
    )
    response = client.get(
        "/api/v1/internal/metrics/instant",
        params={"query": "up"},
        headers={"apikey": MODERATOR_KEY},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["result_type"] == "vector"
    assert payload["samples"][0]["metric"] == {"job": "ai-horde"}


def test_internal_metrics_instant_rejects_invalid_query(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/internal/metrics/instant",
        params={"query": "up;evil"},
        headers={"apikey": MODERATOR_KEY},
    )
    assert response.status_code == 400
