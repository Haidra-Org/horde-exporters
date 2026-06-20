"""Regression test for scheduler wiring.

A probe job added with ``next_run_time=None`` is added *paused* — APScheduler
never runs it, so the prober would silently never probe. This test guards that
``build_scheduler`` schedules every probe with a real next-run time.
"""

from __future__ import annotations

import httpx
import pytest

from horde_status_prober.config import ProberSettings
from horde_status_prober.main import build_probes, build_scheduler
from horde_status_prober.pusher import AlertsPusher


@pytest.mark.asyncio
async def test_probe_jobs_are_scheduled_not_paused() -> None:
    settings = ProberSettings(prober_shared_secret="testsecret")  # type: ignore[arg-type]
    async with (
        httpx.AsyncClient() as aihorde,
        httpx.AsyncClient() as alerts,
    ):
        pusher = AlertsPusher(settings=settings, client=alerts)
        scheduler = build_scheduler(settings, aihorde, pusher)
        scheduler.start()
        try:
            jobs = scheduler.get_jobs()
            assert {job.id for job in jobs} == {probe.name for probe, _ in build_probes()}
            assert len(jobs) == 6
            # next_run_time is None only for paused jobs — exactly the bug.
            assert all(job.next_run_time is not None for job in jobs)
        finally:
            scheduler.shutdown(wait=False)
