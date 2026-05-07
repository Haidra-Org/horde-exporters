"""Integration tests for public routes (unauthenticated)."""

from __future__ import annotations

import httpx
import respx  # type: ignore[import-not-found]
from fastapi.testclient import TestClient

ALERTMANAGER = "http://alertmanager.test"


def _alerts_payload(extra_labels: dict[str, str] | None = None) -> list[dict[str, object]]:
    labels = {
        "alertname": "DiskFillingUp",
        "severity": "warning",
        "component": "storage",
        "service": "ai-horde",
        "instance": "10.0.0.5:9100",
        "pod": "horde-db-0",
        "__name__": "up",
    }
    if extra_labels:
        labels.update(extra_labels)
    return [
        {
            "fingerprint": "abc123",
            "startsAt": "2025-01-01T00:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "updatedAt": "2025-01-01T00:01:00Z",
            "status": {"state": "active"},
            "labels": labels,
            "annotations": {
                "summary": "Disk usage high",
                "description": "Detailed runbook info that must NOT leak.",
                "runbook_url": "https://internal.example/runbook",
            },
            "generatorURL": "https://internal.example/graph",
        },
    ]


def test_public_incidents_strips_internal_keys(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{ALERTMANAGER}/api/v2/alerts").mock(
        return_value=httpx.Response(200, json=_alerts_payload()),
    )

    response = client.get("/api/v1/public/incidents")
    assert response.status_code == 200
    body = response.text
    for forbidden in ("instance", "pod", "__name__", "description", "runbook_url", "generatorURL"):
        assert forbidden not in body
    payload = response.json()
    assert payload["active"][0]["name"] == "DiskFillingUp"
    assert payload["active"][0]["summary"] == "Disk usage high"


def test_public_silences_does_not_attribute_creators(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{ALERTMANAGER}/api/v2/silences").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "silence-1",
                    "status": {"state": "active"},
                    "matchers": [{"name": "component", "value": "frontpage", "isRegex": False}],
                    "startsAt": "2025-01-01T00:00:00Z",
                    "endsAt": "2025-01-02T00:00:00Z",
                    "createdBy": "alice@haidra",
                    "comment": "private maintenance comment",
                },
            ],
        ),
    )

    response = client.get("/api/v1/public/silences")
    assert response.status_code == 200
    body = response.text
    assert "alice" not in body
    assert "private maintenance" not in body
    payload = response.json()
    assert payload["active_silences"] == 1


def test_public_status_returns_overall(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{ALERTMANAGER}/api/v2/alerts").mock(
        return_value=httpx.Response(200, json=[]),
    )
    response = client.get("/api/v1/public/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["overall"] in {"ok", "unknown"}


def test_public_status_503_when_alertmanager_down(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{ALERTMANAGER}/api/v2/alerts").mock(
        return_value=httpx.Response(503, text="boom"),
    )
    response = client.get("/api/v1/public/status")
    assert response.status_code == 503
