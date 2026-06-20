"""Storage for blackbox-prober samples."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_horde_service_alerts.db.models import ProbeResult
from ai_horde_service_alerts.db.types import ProbeOutcome
from ai_horde_service_alerts.models.internal import ProbeResultDetail


class _RowcountResult(Protocol):
    """Protocol for SQLAlchemy result objects exposing ``rowcount``."""

    rowcount: int | None


class ProbeResultRepository:
    """Reads and writes blackbox prober samples."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to an open async session."""
        self._session = session

    async def record(
        self,
        *,
        probe_name: str,
        component_id: str,
        outcome: ProbeOutcome,
        observed_at: datetime,
        latency_ms: int | None = None,
        detail: ProbeResultDetail | None = None,
    ) -> ProbeResult:
        """Persist a single probe sample. Idempotent on (probe_name, observed_at)."""
        row = ProbeResult(
            probe_name=probe_name,
            component_id=component_id,
            outcome=outcome,
            observed_at=observed_at.astimezone(UTC),
            latency_ms=latency_ms,
            detail=detail.model_dump(exclude_none=True) if detail is not None else None,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def latest_per_component(
        self,
        *,
        freshness: timedelta | None = None,
        now: datetime | None = None,
    ) -> dict[str, ProbeResult]:
        """Return the freshest probe sample per component (only those within ``freshness``)."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        stmt = select(ProbeResult).order_by(ProbeResult.observed_at.desc())
        result = await self._session.execute(stmt)
        latest: dict[str, ProbeResult] = {}
        for row in result.scalars():
            if freshness is not None and (moment - row.observed_at) > freshness:
                continue
            if row.component_id not in latest:
                latest[row.component_id] = row
        return latest

    async def recent(
        self,
        *,
        component_id: str | None = None,
        since: datetime | None = None,
        limit: int = 200,
    ) -> Sequence[ProbeResult]:
        """Return recent probe samples for the admin view."""
        stmt = select(ProbeResult).order_by(ProbeResult.observed_at.desc()).limit(limit)
        if component_id is not None:
            stmt = stmt.where(ProbeResult.component_id == component_id)
        if since is not None:
            stmt = stmt.where(ProbeResult.observed_at >= since.astimezone(UTC))
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def trim_older_than(self, *, cutoff: datetime) -> int:
        """Delete rows older than ``cutoff`` and return the deleted count."""
        stmt = delete(ProbeResult).where(ProbeResult.observed_at < cutoff.astimezone(UTC))
        result = await self._session.execute(stmt)
        rowcount_result = cast(_RowcountResult, result)
        return int(rowcount_result.rowcount or 0)
