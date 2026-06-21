"""Integration tests for the public /stats route and graceful degradation."""

from __future__ import annotations

import httpx
import respx  # type: ignore[import-not-found]
from fastapi.testclient import TestClient

MIMIR = "http://mimir.test"

_NULL_FIELDS = {
    "active_image_workers",
    "active_text_workers",
    "queued_image_requests",
    "queued_text_requests",
    "queue_drain_image_seconds",
    "queue_drain_text_seconds",
    "images_generated_day",
    "images_generated_month",
    "tokens_generated_day",
}


def _vector(value: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "success",
            "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1.0, value]}]},
        },
    )


def test_public_stats_maps_values_and_is_unauthenticated(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{MIMIR}/prometheus/api/v1/query").mock(return_value=_vector("5"))
    # No apikey header: the stats strip is part of the public surface.
    response = client.get("/api/v1/public/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["active_image_workers"] == 5
    assert body["queue_drain_image_seconds"] == 5.0
    assert body["images_generated_day"] == 5
    # Alchemy worker count has no backing series and is never queried.
    assert body["active_alchemy_workers"] is None
    assert body["generated_at"]


def test_public_stats_degrades_to_nulls_when_mimir_unavailable(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{MIMIR}/prometheus/api/v1/query").mock(return_value=httpx.Response(503))
    response = client.get("/api/v1/public/stats")
    # Never 5xx the public page on a metrics outage; serve nulls instead.
    assert response.status_code == 200
    body = response.json()
    for field in _NULL_FIELDS:
        assert body[field] is None, field


def test_public_stats_uses_public_tenant_header(
    client: TestClient,
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(f"{MIMIR}/prometheus/api/v1/query").mock(return_value=_vector("1"))
    client.get("/api/v1/public/stats")
    assert route.called
    assert route.calls.last.request.headers["X-Scope-OrgID"] == "ai-horde-public"
