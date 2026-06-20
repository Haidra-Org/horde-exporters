"""Component status changelog reads + writes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_horde_service_alerts.db.models import ComponentStatusHistory
from ai_horde_service_alerts.db.types import (
    STATUS_RANK,
    ComponentStatusValue,
    HistorySource,
    HistoryTrigger,
)


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
    status_level: int  # 0 ok, 1 minor (degraded/unknown), 2 major (partial/down), 3 maintenance
    operational_seconds: int
    degraded_seconds: int
    down_seconds: int
    maintenance_seconds: int
    unknown_seconds: int


_LEVEL_BY_STATUS: dict[ComponentStatusValue, int] = {
    ComponentStatusValue.OPERATIONAL: 0,
    ComponentStatusValue.UNKNOWN: 1,
    ComponentStatusValue.DEGRADED: 1,
    ComponentStatusValue.PARTIAL: 2,
    ComponentStatusValue.DOWN: 2,
    ComponentStatusValue.MAINTENANCE: 3,
}


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
            worst_level = 0
            worst_status = ComponentStatusValue.OPERATIONAL
            for status, secs in seconds.items():
                if secs > 0 and STATUS_RANK[status] > STATUS_RANK[worst_status]:
                    worst_status = status
            worst_level = _LEVEL_BY_STATUS[worst_status]
            buckets.append(
                DailyBucket(
                    date=day_start.date().isoformat(),
                    status_level=worst_level,
                    operational_seconds=seconds[ComponentStatusValue.OPERATIONAL],
                    degraded_seconds=seconds[ComponentStatusValue.DEGRADED],
                    down_seconds=seconds[ComponentStatusValue.DOWN] + seconds[ComponentStatusValue.PARTIAL],
                    maintenance_seconds=seconds[ComponentStatusValue.MAINTENANCE],
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
        """Return uptime% over the trailing window, excluding maintenance time.

        Returns ``None`` when no history exists at all (so callers can render
        ``—`` instead of a misleading ``0%``).
        """
        buckets = await self.daily_buckets(component_id, days=days, now=now)
        operational = sum(b.operational_seconds for b in buckets)
        maintenance = sum(b.maintenance_seconds for b in buckets)
        non_maintenance = days * 86_400 - maintenance
        if non_maintenance <= 0:
            return None
        observed = operational + sum((b.degraded_seconds + b.down_seconds + b.unknown_seconds) for b in buckets)
        if observed == 0:
            return None
        return round(operational / non_maintenance * 100.0, 4)

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
