"""Smoke tests for probe + pusher wiring."""

from __future__ import annotations

from typing import Any, TypedDict

import httpx
import pytest
import respx

from horde_status_prober.config import ProberSettings
from horde_status_prober.probes.api_heartbeat import ApiHeartbeatProbe
from horde_status_prober.probes.image_workers import ImageWorkersProbe
from horde_status_prober.probes.base import ProbeOutcome, ProbeResultDetail
from horde_status_prober.pusher import AlertsPusher


class _CapturedRequest(TypedDict, total=False):
    """Represents request properties captured from the mocked push endpoint."""

    url: str
    secret: str | None
    body: bytes


def _settings() -> ProberSettings:
    return ProberSettings(
        prober_shared_secret="testsecret",  # type: ignore[arg-type]
        aihorde_base_url="https://horde.example/api",
        alerts_base_url="https://alerts.example/api/v1",
    )


@pytest.mark.asyncio
async def test_api_heartbeat_ok() -> None:
    settings = _settings()
    async with (
        httpx.AsyncClient(base_url=settings.aihorde_base_url) as client,
        respx.mock(
            base_url=settings.aihorde_base_url,
        ) as router,
    ):
        router.get("/v2/status/heartbeat").respond(200, json={"message": "ok"})
        result = await ApiHeartbeatProbe().run(client)
        assert result.outcome is ProbeOutcome.OK
        assert result.detail is None
    assert result.component_id == "api"


@pytest.mark.asyncio
async def test_api_heartbeat_down_on_500() -> None:
    settings = _settings()
    async with (
        httpx.AsyncClient(base_url=settings.aihorde_base_url) as client,
        respx.mock(
            base_url=settings.aihorde_base_url,
        ) as router,
    ):
        router.get("/v2/status/heartbeat").respond(500, text="bad")
        result = await ApiHeartbeatProbe().run(client)
        assert result.outcome is ProbeOutcome.DOWN
        assert result.detail == ProbeResultDetail(status_code=500)


@pytest.mark.asyncio
async def test_image_workers_degraded_when_pool_drains() -> None:
    settings = _settings()
    async with (
        httpx.AsyncClient(base_url=settings.aihorde_base_url) as client,
        respx.mock(
            base_url=settings.aihorde_base_url,
        ) as router,
    ):
        router.get("/v2/status/performance").respond(
            200,
            json={"worker_count": 2, "text_worker_count": 4},
        )
        result = await ImageWorkersProbe().run(client)
    assert result.outcome is ProbeOutcome.DEGRADED
    assert result.detail == ProbeResultDetail(worker_count=2)


@pytest.mark.asyncio
async def test_pusher_posts_with_secret_header() -> None:
    settings = _settings()
    captured: _CapturedRequest = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["secret"] = request.headers.get("x-prober-secret")
        captured["body"] = request.content
        return httpx.Response(202)

    async with httpx.AsyncClient() as client, respx.mock() as router:
        router.post(f"{settings.alerts_base_url}/internal/probe-results").mock(
            side_effect=_capture,
        )
        pusher = AlertsPusher(settings=settings, client=client)
        result = await ApiHeartbeatProbe().run(_DummyHorde()) # pyrefly: ignore
        ok = await pusher.push(result)

    assert ok is True
    assert captured["secret"] == "testsecret"
    assert captured["url"].endswith("/internal/probe-results")
    assert pusher.consecutive_failures == 0


class _DummyHorde:
    """A tiny stand-in that returns a healthy heartbeat without real HTTP."""

    async def get(self, _path: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(200, json={"message": "ok"})


@pytest.mark.asyncio
async def test_pusher_records_consecutive_failures() -> None:
    settings = _settings()
    async with httpx.AsyncClient() as client, respx.mock() as router:
        router.post(f"{settings.alerts_base_url}/internal/probe-results").respond(503)
        pusher = AlertsPusher(settings=settings, client=client)
        for _ in range(settings.max_consecutive_push_failures):
            ok = await pusher.push(
                await ApiHeartbeatProbe().run(_DummyHorde()), # pyrefly: ignore
            )
            assert ok is False
    assert pusher.is_unhealthy
