"""Unit tests for the app's background-task loop driver.

``create_app`` runs the status evaluator and maintenance runner as
``asyncio`` tasks wrapping :func:`_periodic`. The evaluator's *logic* is
covered by the integration tests; this proves the loop *mechanism*: it runs
the callable repeatedly, survives a failing tick, and stops on cancellation.
"""

from __future__ import annotations

import asyncio

import pytest

from ai_horde_service_alerts.app import _periodic


async def test_periodic_repeats_survives_errors_and_cancels() -> None:
    calls = 0

    async def factory() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first tick blows up — loop must keep going")

    task = asyncio.create_task(_periodic(factory, interval=0.01, name="test"))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # It kept ticking after the exception in tick #1 (not killed by it).
    assert calls >= 3
