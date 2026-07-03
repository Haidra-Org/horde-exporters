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
from ai_horde_service_alerts.db.repositories.history import HistoryRepository, classify_day_level
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


@pytest.mark.asyncio
async def test_uptime_counts_degraded_as_available(db_session: AsyncSession) -> None:
    """Degraded is slow-but-serving: it counts as up, not as downtime."""
    await _seed_component(db_session)
    start = NOON_TODAY - timedelta(days=1)
    midpoint = start + timedelta(hours=12)
    db_session.add_all(
        [
            _slice(ComponentStatusValue.OPERATIONAL, start, midpoint),
            _slice(ComponentStatusValue.DEGRADED, midpoint, NOON_TODAY),
        ],
    )
    await db_session.flush()

    repo = HistoryRepository(db_session)
    uptime = await repo.uptime_percent(COMPONENT_ID, days=2, now=NOON_TODAY)

    assert uptime == pytest.approx(100.0, abs=0.5)


# --- Duration-weighted day-bar classification -----------------------------
#
# These pin the fix for the production symptom: a whole day painted red/orange
# because a single flapping scrape recorded a handful of down/degraded seconds.

DAY = 86_400


def test_brief_down_blip_stays_green() -> None:
    """A 15s down blip on an otherwise-operational day must not go major."""
    assert (
        classify_day_level(
            operational_seconds=DAY - 15,
            degraded_seconds=0,
            down_seconds=15,
            maintenance_seconds=0,
        )
        == 0
    )


def test_brief_degraded_blip_stays_green() -> None:
    """A few minutes of degraded (below the minor floor) stays green."""
    assert (
        classify_day_level(
            operational_seconds=DAY - 300,
            degraded_seconds=300,
            down_seconds=0,
            maintenance_seconds=0,
        )
        == 0
    )


def test_sustained_down_is_major() -> None:
    """Down time past the 300s / 1% floor escalates the day to major (red)."""
    assert (
        classify_day_level(
            operational_seconds=DAY - 1200,
            degraded_seconds=0,
            down_seconds=1200,
            maintenance_seconds=0,
        )
        == 2
    )


def test_sustained_degraded_is_minor() -> None:
    """Degraded time past the 600s / 5% floor escalates the day to minor (orange)."""
    assert (
        classify_day_level(
            operational_seconds=DAY - 6000,
            degraded_seconds=6000,
            down_seconds=0,
            maintenance_seconds=0,
        )
        == 1
    )


def test_down_outranks_degraded() -> None:
    """A day with both sustained down and degraded reports the worse (major)."""
    assert (
        classify_day_level(
            operational_seconds=DAY - 6000,
            degraded_seconds=5000,
            down_seconds=1000,
            maintenance_seconds=0,
        )
        == 2
    )


def test_no_signal_day_is_maintenance_or_ok() -> None:
    """A day with only maintenance is level 3; a wholly empty day is level 0."""
    assert (
        classify_day_level(
            operational_seconds=0,
            degraded_seconds=0,
            down_seconds=0,
            maintenance_seconds=DAY,
        )
        == 3
    )
    assert (
        classify_day_level(
            operational_seconds=0,
            degraded_seconds=0,
            down_seconds=0,
            maintenance_seconds=0,
        )
        == 0
    )


@pytest.mark.asyncio
async def test_real_world_blip_day_not_painted(db_session: AsyncSession) -> None:
    """Reproduces the live symptom: a 15s down slice must not make the bar red."""
    await _seed_component(db_session)
    day_start = NOON_TODAY.replace(hour=0, minute=0, second=0, microsecond=0)
    down_start = day_start + timedelta(hours=6)
    db_session.add_all(
        [
            _slice(ComponentStatusValue.OPERATIONAL, day_start, down_start),
            _slice(ComponentStatusValue.DOWN, down_start, down_start + timedelta(seconds=15)),
            _slice(ComponentStatusValue.OPERATIONAL, down_start + timedelta(seconds=15), None),
        ],
    )
    await db_session.flush()

    repo = HistoryRepository(db_session)
    buckets = await repo.daily_buckets(COMPONENT_ID, days=1, now=NOON_TODAY)

    today = buckets[-1]
    assert today.down_seconds == 15  # the blip is still recorded...
    assert today.status_level == 0  # ...but the bar stays green
