"""Regression tests for the public 90-day history roll-up.

These reproduce the two production symptoms observed on the live status page:

* an always-yellow trailing bar caused by a short ``unknown`` scrape gap on the
  current (partial) day, even though the component never went degraded/down; and
* uptime collapsing toward ~0% because historical no-data days were counted as
  downtime.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_horde_service_alerts.db.models import Component, ComponentStatusHistory
from ai_horde_service_alerts.db.repositories.history import HistoryRepository
from ai_horde_service_alerts.db.types import Audience, ComponentStatusValue, HistorySource

NOON_TODAY = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
COMPONENT_ID = "api"


async def _seed_component(session: AsyncSession) -> None:
    session.add(
        Component(
            id=COMPONENT_ID,
            name="API",
            description="",
            audience=Audience.PUBLIC,
        ),
    )
    await session.flush()


def _slice(
    status: ComponentStatusValue,
    started_at: datetime,
    ended_at: datetime | None,
) -> ComponentStatusHistory:
    return ComponentStatusHistory(
        component_id=COMPONENT_ID,
        status=status,
        source=HistorySource.PROBER,
        started_at=started_at,
        ended_at=ended_at,
    )


@pytest.mark.asyncio
async def test_trailing_unknown_gap_does_not_paint_bar_minor(db_session: AsyncSession) -> None:
    """A ~71-minute unknown gap today, with no degraded/down time, stays level 0."""
    await _seed_component(db_session)
    day_start = NOON_TODAY.replace(hour=0, minute=0, second=0, microsecond=0)
    gap_start = NOON_TODAY - timedelta(seconds=4254)
    # operational -> unknown (scrape gap) -> operational, all on the current day.
    db_session.add_all(
        [
            _slice(ComponentStatusValue.OPERATIONAL, day_start, gap_start),
            _slice(ComponentStatusValue.UNKNOWN, gap_start, NOON_TODAY - timedelta(seconds=600)),
            _slice(ComponentStatusValue.OPERATIONAL, NOON_TODAY - timedelta(seconds=600), None),
        ],
    )
    await db_session.flush()

    repo = HistoryRepository(db_session)
    buckets = await repo.daily_buckets(COMPONENT_ID, days=1, now=NOON_TODAY)

    today = buckets[-1]
    assert today.degraded_seconds == 0
    assert today.down_seconds == 0
    assert today.maintenance_seconds == 0
    assert today.unknown_seconds > 0  # the gap is recorded...
    assert today.status_level == 0  # ...but it must NOT fold the bar to minor


@pytest.mark.asyncio
async def test_uptime_excludes_no_data_days(db_session: AsyncSession) -> None:
    """No-data historical days must not count as downtime (was driving ~0.5%)."""
    await _seed_component(db_session)
    # The only signal in a 90-day window is one fully-operational slice covering
    # the last ~2 days. Everything before it is a genuine no-data gap.
    db_session.add(
        _slice(ComponentStatusValue.OPERATIONAL, NOON_TODAY - timedelta(days=2), None),
    )
    await db_session.flush()

    repo = HistoryRepository(db_session)
    uptime = await repo.uptime_percent(COMPONENT_ID, days=90, now=NOON_TODAY)

    assert uptime == 100.0


@pytest.mark.asyncio
async def test_uptime_none_when_no_signal_at_all(db_session: AsyncSession) -> None:
    """A window with no operational/degraded/down signal returns None, not 0/100."""
    await _seed_component(db_session)
    repo = HistoryRepository(db_session)

    uptime = await repo.uptime_percent(COMPONENT_ID, days=90, now=NOON_TODAY)

    assert uptime is None


@pytest.mark.asyncio
async def test_uptime_counts_real_downtime(db_session: AsyncSession) -> None:
    """Sanity: actual down time is reflected in the ratio (operational / signal)."""
    await _seed_component(db_session)
    start = NOON_TODAY - timedelta(days=1)
    midpoint = start + timedelta(hours=12)
    db_session.add_all(
        [
            _slice(ComponentStatusValue.OPERATIONAL, start, midpoint),
            _slice(ComponentStatusValue.DOWN, midpoint, NOON_TODAY),
        ],
    )
    await db_session.flush()

    repo = HistoryRepository(db_session)
    uptime = await repo.uptime_percent(COMPONENT_ID, days=2, now=NOON_TODAY)

    assert uptime == pytest.approx(50.0, abs=0.5)
