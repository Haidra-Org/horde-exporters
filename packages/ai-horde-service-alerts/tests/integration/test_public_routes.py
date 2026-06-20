"""Integration tests for public routes (unauthenticated, structural-only).

The redesigned public surface never reflects raw Alertmanager prose. These
tests verify the four public endpoints (`components`, `incidents`,
`maintenance`, `history`) and the leak-prevention property: no IPs, internal
alertnames, runbook URLs, generator URLs, or Watchdog labels can appear in
the response body for any input.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

PUBLIC_COMPONENT_IDS = {"api", "image", "text", "alchemy", "workers", "webhooks"}
LEAK_FORBIDDEN_TOKENS = (
    "10.0.0",
    "Watchdog",
    "runbook_url",
    "generatorURL",
    "DiskFillingUp",
    "__name__",
)


def _assert_no_leaks(body: str) -> None:
    for token in LEAK_FORBIDDEN_TOKENS:
        assert token not in body, f"leaked internal token {token!r} in public response"


def test_public_components_lists_seeded_components(client: TestClient) -> None:
    response = client.get("/api/v1/public/components")
    assert response.status_code == 200
    payload = response.json()
    assert {"components", "overall", "generated_at"} <= payload.keys()
    ids = {c["id"] for c in payload["components"]}
    assert ids >= PUBLIC_COMPONENT_IDS
    for component in payload["components"]:
        assert {"id", "name", "description", "status"} <= component.keys()
        assert component["status"] in {
            "operational",
            "degraded",
            "partial",
            "down",
            "maintenance",
            "unknown",
        }
    _assert_no_leaks(response.text)


def test_public_incidents_returns_empty_lists_initially(client: TestClient) -> None:
    response = client.get("/api/v1/public/incidents")
    assert response.status_code == 200
    payload = response.json()
    assert payload["active"] == []
    assert payload["recent_resolved"] == []
    assert "generated_at" in payload
    _assert_no_leaks(response.text)


def test_public_maintenance_returns_empty_initially(client: TestClient) -> None:
    response = client.get("/api/v1/public/maintenance")
    assert response.status_code == 200
    payload = response.json()
    assert payload["windows"] == []
    assert "generated_at" in payload
    _assert_no_leaks(response.text)


def test_public_history_for_known_component(client: TestClient) -> None:
    response = client.get("/api/v1/public/history", params={"component": "image", "days": 30})
    assert response.status_code == 200
    payload = response.json()
    assert payload["component_id"] == "image"
    assert payload["days"] == 30
    assert isinstance(payload["buckets"], list)
    _assert_no_leaks(response.text)


def test_public_history_404_for_unknown_component(client: TestClient) -> None:
    response = client.get("/api/v1/public/history", params={"component": "nope-xyz"})
    assert response.status_code == 404


def test_public_history_rejects_out_of_range_days(client: TestClient) -> None:
    response = client.get("/api/v1/public/history", params={"component": "image", "days": 9999})
    assert response.status_code == 422
