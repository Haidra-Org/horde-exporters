"""Storage for blackbox-prober samples."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from sqlalchemy import delete, func, select
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
        """Return the freshest probe sample per component (only those within ``freshness``).

        Bounded on the database side: the freshness cutoff is applied in SQL
        (index ``ix_probe_results_observed_at``) and a window function picks the
        newest row per component, so the result set is one row per component
        regardless of how large ``probe_results`` has grown. The previous
        implementation selected the whole table every evaluator tick and
        filtered in Python, which pinned a CPU core for ~20 s per tick and
        allocated ~500 MB once the table reached a few hundred thousand rows.
        """
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        ranked = select(
            ProbeResult.id.label("id"),
            func.row_number()
            .over(
                partition_by=ProbeResult.component_id,
                order_by=ProbeResult.observed_at.desc(),
            )
            .label("rn"),
        )
        if freshness is not None:
            ranked = ranked.where(ProbeResult.observed_at >= moment - freshness)
        ranked_sq = ranked.subquery("ranked")
        stmt = select(ProbeResult).join(ranked_sq, ranked_sq.c.id == ProbeResult.id).where(ranked_sq.c.rn == 1)
        result = await self._session.execute(stmt)
        return {row.component_id: row for row in result.scalars()}

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
