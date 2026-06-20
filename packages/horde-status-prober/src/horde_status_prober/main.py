"""Prober entrypoint: schedules probes and exposes a ``/healthz`` endpoint.

This module is the package's CLI entrypoint. It wires:

* one :class:`httpx.AsyncClient` per upstream (AI Horde + alerts service),
* an :class:`apscheduler.schedulers.asyncio.AsyncIOScheduler` running each
  probe on its own interval,
* a tiny FastAPI app exposing ``GET /healthz`` so a container orchestrator
  can detect a stuck or failing prober.

The module is intentionally kept dependency-light and synchronous-friendly
so it can be unit tested without spinning up the scheduler.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Literal, TypedDict

import httpx
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from horde_status_prober.config import ProberSettings
from horde_status_prober.probes.alchemy_smoke import AlchemySmokeProbe
from horde_status_prober.probes.api_heartbeat import ApiHeartbeatProbe
from horde_status_prober.probes.api_performance import ApiPerformanceProbe
from horde_status_prober.probes.base import Probe
from horde_status_prober.probes.image_workers import ImageWorkersProbe
from horde_status_prober.probes.text_workers import TextWorkersProbe
from horde_status_prober.probes.webhooks_smoke import WebhooksSmokeProbe
from horde_status_prober.pusher import AlertsPusher

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_logger = logging.getLogger(__name__)


class HealthzResponse(TypedDict):
    """Represents the JSON response body returned by ``GET /healthz``."""

    status: Literal["ok", "degraded"]
    consecutive_failures: int


def build_probes() -> list[tuple[Probe, str]]:
    """Return ``(probe, settings-attr-for-interval)`` for every probe."""
    return [
        (ApiHeartbeatProbe(), "api_heartbeat_interval"),
        (ApiPerformanceProbe(), "api_performance_interval"),
        (ImageWorkersProbe(), "image_workers_interval"),
        (TextWorkersProbe(), "text_workers_interval"),
        (WebhooksSmokeProbe(), "webhooks_smoke_interval"),
        (AlchemySmokeProbe(), "alchemy_smoke_interval"),
    ]


def build_runner(
    probe: Probe,
    aihorde: httpx.AsyncClient,
    pusher: AlertsPusher,
) -> Callable[[], Awaitable[None]]:
    """Build the coroutine the scheduler will invoke on each tick."""

    async def _run() -> None:
        try:
            result = await probe.run(aihorde)
        except Exception:  # noqa: BLE001 - surface any probe bug, keep scheduler alive
            _logger.exception("Probe %s raised", probe.name)
            return
        await pusher.push(result)

    return _run


def build_healthz_app(pusher: AlertsPusher) -> FastAPI:
    """Tiny FastAPI app that reports the pusher's health."""
    app = FastAPI(title="horde-status-prober", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz() -> HealthzResponse:
        return {
            "status": "ok" if not pusher.is_unhealthy else "degraded",
            "consecutive_failures": pusher.consecutive_failures,
        }

    return app


@asynccontextmanager
async def _lifespan(
    settings: ProberSettings,
) -> AsyncIterator[tuple[AsyncIOScheduler, AlertsPusher]]:
    """Wire the scheduler and HTTP clients with proper teardown."""
    async with AsyncExitStack() as stack:
        aihorde = await stack.enter_async_context(
            httpx.AsyncClient(
                base_url=settings.aihorde_base_url.rstrip("/"),
                timeout=settings.aihorde_timeout_seconds,
                headers={"user-agent": settings.user_agent},
            ),
        )
        alerts = await stack.enter_async_context(
            httpx.AsyncClient(
                timeout=settings.alerts_timeout_seconds,
                headers={"user-agent": settings.user_agent},
            ),
        )
        pusher = AlertsPusher(settings=settings, client=alerts)

        scheduler = AsyncIOScheduler()
        for probe, attr in build_probes():
            interval = getattr(settings, attr)
            scheduler.add_job(
                build_runner(probe, aihorde, pusher),
                trigger="interval",
                seconds=interval,
                id=probe.name,
                next_run_time=None,
                max_instances=1,
                coalesce=True,
            )
        scheduler.start()
        try:
            yield scheduler, pusher
        finally:
            scheduler.shutdown(wait=False)


async def _serve_healthz(app: FastAPI, settings: ProberSettings) -> None:
    config = uvicorn.Config(
        app,
        host=settings.healthz_host,
        port=settings.healthz_port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run(settings: ProberSettings | None = None) -> None:
    """Top-level coroutine wiring the lifespan and the healthz server."""
    settings = settings or ProberSettings()  # type: ignore[call-arg]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    async with _lifespan(settings) as (_scheduler, pusher):
        app = build_healthz_app(pusher)
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                # Windows / restricted environments - fall back to KeyboardInterrupt.
                pass
        serve_task = asyncio.create_task(_serve_healthz(app, settings))
        wait_task = asyncio.create_task(stop_event.wait())
        done, pending = await asyncio.wait(
            {serve_task, wait_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, asyncio.CancelledError):
                raise exc


def main() -> None:
    """Console-script entrypoint."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
