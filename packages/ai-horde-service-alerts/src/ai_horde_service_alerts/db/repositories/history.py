"""Component status changelog reads + writes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_horde_service_alerts.db.models import ComponentStatusHistory
from ai_horde_service_alerts.db.types import (
    ComponentStatusValue,
    HistorySource,
    HistoryTrigger,
)


@dataclass(frozen=True, slots=True)
class BucketThresholds:
    """Duration thresholds that decide a day bar's colour.

    A day escalates only when a *meaningful* amount of the day's observed signal
    was bad, not merely because a single flapping scrape recorded a few seconds
    of down/degraded time. Each level uses an absolute floor OR a fraction of the
    day's observed signal (whichever is larger), so brief blips on a busy day and
    a couple of bad samples on a quiet day both stay green — the sub-threshold
    remainder is surfaced by the front-end "flapping" marker instead.
    """

    #: Cumulative down/partial seconds that make a day "major" (red).
    major_down_floor_seconds: int = 300
    #: ...or this fraction of the day's observed signal, whichever is larger.
    major_down_fraction: float = 0.01
    #: Cumulative degraded seconds that make a day "minor" (orange).
    minor_degraded_floor_seconds: int = 600
    #: ...or this fraction of the day's observed signal, whichever is larger.
    minor_degraded_fraction: float = 0.05


DEFAULT_BUCKET_THRESHOLDS = BucketThresholds()


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """Lightweight projection of a row from ``component_status_history``."""

    component_id: str
    status: ComponentStatusValue
    source: HistorySource
    started_at: datetime
    ended_at: datetime | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class DailyBucket:
    """Daily roll-up used for the public 90-day history bars."""

    date: str  # ISO YYYY-MM-DD
    status_level: int  # 0 ok, 1 minor (degraded), 2 major (partial/down), 3 maintenance
    operational_seconds: int
    degraded_seconds: int
    down_seconds: int
    maintenance_seconds: int
    unknown_seconds: int


def classify_day_level(
    *,
    operational_seconds: int,
    degraded_seconds: int,
    down_seconds: int,
    maintenance_seconds: int,
    thresholds: BucketThresholds = DEFAULT_BUCKET_THRESHOLDS,
) -> int:
    """Return the day bar level (0 ok | 1 minor | 2 major | 3 maintenance).

    Duration-weighted, not worst-observed: a level is raised only when the bad
    time crosses that level's floor/fraction of the day's observed signal.
    Unknown ("no signal") time never counts and is excluded from the signal
    denominator, so a scrape gap can't paint the bar. A day with no real signal
    at all reads as maintenance (3) if it saw maintenance, else ok (0).
    """
    signal = operational_seconds + degraded_seconds + down_seconds
    if signal == 0:
        return 3 if maintenance_seconds > 0 else 0
    major_cut = max(thresholds.major_down_floor_seconds, thresholds.major_down_fraction * signal)
    if down_seconds >= major_cut:
        return 2
    minor_cut = max(thresholds.minor_degraded_floor_seconds, thresholds.minor_degraded_fraction * signal)
    if degraded_seconds >= minor_cut:
        return 1
    return 0


class HistoryRepository:
    """Writes new state slices and reads them back for the public history view."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to an open async session."""
        self._session = session

    async def get_open(self, component_id: str) -> ComponentStatusHistory | None:
        """Return the currently-open slice (``ended_at IS NULL``) for the component."""
        stmt = select(ComponentStatusHistory).where(
            and_(
                ComponentStatusHistory.component_id == component_id,
                ComponentStatusHistory.ended_at.is_(None),
            ),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def transition(
        self,
        *,
        component_id: str,
        new_status: ComponentStatusValue,
        source: HistorySource,
        when: datetime,
        reason: str | None = None,
        triggered_by: HistoryTrigger | None = None,
    ) -> ComponentStatusHistory | None:
        """Close the open slice and open a new one, when the status actually changes.

        Returns the new row, or None if no transition was needed.
        """
        open_row = await self.get_open(component_id)
        if open_row is not None and open_row.status == new_status:
            return None
        if open_row is not None:
            open_row.ended_at = when
        new_row = ComponentStatusHistory(
            component_id=component_id,
            status=new_status,
            source=source,
            started_at=when,
            reason=reason,
            triggered_by=triggered_by,
        )
        self._session.add(new_row)
        await self._session.flush()
        return new_row

    async def fetch_window(
        self,
        component_id: str,
        *,
        since: datetime,
        until: datetime,
    ) -> Sequence[HistoryRow]:
        """Return slices that overlap the half-open window ``[since, until)``."""
        stmt = (
            select(ComponentStatusHistory)
            .where(ComponentStatusHistory.component_id == component_id)
            .where(ComponentStatusHistory.started_at < until)
            .where(
                (ComponentStatusHistory.ended_at.is_(None)) | (ComponentStatusHistory.ended_at > since),
            )
            .order_by(ComponentStatusHistory.started_at)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [
            HistoryRow(
                component_id=row.component_id,
                status=row.status,
                source=row.source,
                started_at=row.started_at,
                ended_at=row.ended_at,
                reason=row.reason,
            )
            for row in rows
        ]

    async def daily_buckets(
        self,
        component_id: str,
        *,
        days: int,
        now: datetime | None = None,
        thresholds: BucketThresholds = DEFAULT_BUCKET_THRESHOLDS,
    ) -> list[DailyBucket]:
        """Compute one ``DailyBucket`` per day for the trailing ``days`` days."""
        anchor = (now or datetime.now(tz=UTC)).astimezone(UTC)
        end = anchor.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        start = end - timedelta(days=days)
        rows = await self.fetch_window(component_id, since=start, until=end)
        buckets: list[DailyBucket] = []
        for day_index in range(days):
            day_start = start + timedelta(days=day_index)
            day_end = day_start + timedelta(days=1)
            seconds: dict[ComponentStatusValue, int] = dict.fromkeys(ComponentStatusValue, 0)
            for row in rows:
                slice_start = max(row.started_at, day_start)
                slice_end = min(row.ended_at or anchor, day_end)
                if slice_end <= slice_start:
                    continue
                seconds[row.status] += int((slice_end - slice_start).total_seconds())
            operational = seconds[ComponentStatusValue.OPERATIONAL]
            degraded = seconds[ComponentStatusValue.DEGRADED]
            down = seconds[ComponentStatusValue.DOWN] + seconds[ComponentStatusValue.PARTIAL]
            maintenance = seconds[ComponentStatusValue.MAINTENANCE]
            level = classify_day_level(
                operational_seconds=operational,
                degraded_seconds=degraded,
                down_seconds=down,
                maintenance_seconds=maintenance,
                thresholds=thresholds,
            )
            buckets.append(
                DailyBucket(
                    date=day_start.date().isoformat(),
                    status_level=level,
                    operational_seconds=operational,
                    degraded_seconds=degraded,
                    down_seconds=down,
                    maintenance_seconds=maintenance,
                    unknown_seconds=seconds[ComponentStatusValue.UNKNOWN],
                ),
            )
        return buckets

    async def uptime_percent(
        self,
        component_id: str,
        *,
        days: int,
        now: datetime | None = None,
    ) -> float | None:
        """Return uptime% over the trailing window.

        "Uptime" here means *available* time: operational and degraded both count
        as up, because degraded is slow-but-serving, not an outage. Only hard
        down/partial time is subtracted. The denominator is time for which we
        have a real status signal (operational + degraded + down); maintenance,
        unknown, and no-data days are excluded outright, since counting them
        would conflate "we weren't watching" / "scheduled maintenance" with
        downtime. The current day is therefore self-correcting too — its
        not-yet-elapsed remainder has no signal and so never enters the
        denominator.

        Returns ``None`` when there is no signal at all in the window (so callers
        can render ``—`` instead of a misleading ``0%`` or ``100%``).
        """
        buckets = await self.daily_buckets(component_id, days=days, now=now)
        operational = sum(b.operational_seconds for b in buckets)
        degraded = sum(b.degraded_seconds for b in buckets)
        down = sum(b.down_seconds for b in buckets)
        signal = operational + degraded + down
        if signal == 0:
            return None
        return round((operational + degraded) / signal * 100.0, 4)

    async def close_open_slice_at(
        self,
        component_id: str,
        *,
        at: datetime,
    ) -> None:
        """Close any open slice for the component at ``at``. Used when seeding."""
        stmt = (
            update(ComponentStatusHistory)
            .where(
                ComponentStatusHistory.component_id == component_id,
                ComponentStatusHistory.ended_at.is_(None),
            )
            .values(ended_at=at)
        )
        await self._session.execute(stmt)
