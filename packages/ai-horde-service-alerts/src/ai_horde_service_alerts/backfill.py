"""One-shot Mimir → component_status_history backfill.

Reconstructs the last ``backfill_window_days`` of public component status
history from Prometheus ``ALERTS{alertstate="firing"}`` samples in Mimir.

Strategy
--------
For each curated rule in :class:`AlertMapping`, we issue a Prometheus
range query for ``ALERTS{alertname=..., alertstate="firing", ...}`` over
the configured window using a fixed ``step``. Consecutive samples that
sit no more than ``2 * step`` apart are coalesced into a single firing
interval ``[started_at, ended_at]``. Each interval is then persisted as
a closed :class:`ComponentStatusHistory` row at
``source = HistorySource.BACKFILL`` with the rule's curated status
(typically ``degraded`` or ``partial``).

Idempotency
-----------
Each (component_id, started_at, source=BACKFILL) row is checked for
existence before insert, so re-running the backfill on the same window
is a no-op.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ai_horde_service_alerts.clients.errors import UpstreamUnavailable
from ai_horde_service_alerts.clients.mimir import MimirClient
from ai_horde_service_alerts.db.models import ComponentStatusHistory
from ai_horde_service_alerts.db.session import DatabaseBundle
from ai_horde_service_alerts.db.types import BackfillHistoryTrigger, ComponentStatusValue, HistorySource
from ai_horde_service_alerts.services.alert_mapping import AlertMapping, AlertMatch

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BackfillInterval:
    """One reconstructed firing interval ready to be persisted."""

    component_id: str
    status: ComponentStatusValue
    started_at: datetime
    ended_at: datetime
    alertname: str


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    """Result of one :func:`run_backfill` invocation."""

    intervals_found: int
    intervals_inserted: int
    intervals_skipped: int


async def run_backfill(
    *,
    database: DatabaseBundle,
    mimir: MimirClient,
    alert_mapping: AlertMapping,
    window_days: int,
    step_seconds: float = 60.0,
    now: datetime | None = None,
) -> BackfillSummary:
    """Reconstruct historical status slices from firing-alert samples in Mimir.

    Args:
        database: DB bundle for writes.
        mimir: Mimir client for range queries.
        alert_mapping: Curated alertname → component map.
        window_days: How many days of history to walk back (1..400).
        step_seconds: Range-query step. Larger values are cheaper but coarser.
        now: Override "now" for tests; defaults to ``datetime.now(UTC)``.
    """
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
    start_dt = moment - timedelta(days=window_days)
    start_ts = start_dt.timestamp()
    end_ts = moment.timestamp()
    coalesce_gap = step_seconds * 2.0

    intervals: list[BackfillInterval] = []
    for match in _iter_unique_matches(alert_mapping):
        promql = _build_alerts_query(match)
        try:
            result = await mimir.query_range(
                promql,
                start=start_ts,
                end=end_ts,
                step=step_seconds,
            )
        except UpstreamUnavailable as exc:
            logger.warning("backfill: mimir range query failed for %s: %s", match.alertname, exc)
            continue
        for series in result.series:
            for interval in _coalesce_intervals(
                [ts for ts, _ in series.values],
                coalesce_gap_seconds=coalesce_gap,
            ):
                intervals.append(
                    BackfillInterval(
                        component_id=match.component_id,
                        status=match.status,
                        started_at=interval[0],
                        ended_at=interval[1],
                        alertname=match.alertname,
                    ),
                )

    inserted = 0
    skipped = 0
    async with database.session() as session:
        for interval in intervals:
            stmt = select(ComponentStatusHistory.id).where(
                ComponentStatusHistory.component_id == interval.component_id,
                ComponentStatusHistory.source == HistorySource.BACKFILL,
                ComponentStatusHistory.started_at == interval.started_at,
            )
            existing = (await session.execute(stmt)).scalars().first()
            if existing is not None:
                skipped += 1
                continue
            backfill_trigger: BackfillHistoryTrigger = {"alertname": interval.alertname}
            session.add(
                ComponentStatusHistory(
                    component_id=interval.component_id,
                    status=interval.status,
                    source=HistorySource.BACKFILL,
                    started_at=interval.started_at,
                    ended_at=interval.ended_at,
                    reason=f"backfill from alert {interval.alertname}",
                    triggered_by=backfill_trigger,
                ),
            )
            inserted += 1

    summary = BackfillSummary(
        intervals_found=len(intervals),
        intervals_inserted=inserted,
        intervals_skipped=skipped,
    )
    logger.info("backfill complete: %s", summary)
    return summary


def _iter_unique_matches(mapping: AlertMapping) -> Iterable[AlertMatch]:
    seen: set[tuple[str, str, str, tuple[tuple[str, str], ...]]] = set()
    for alertname in mapping.known_alertnames():
        for match in mapping._by_alertname.get(alertname, ()):
            key = (match.alertname, match.component_id, match.status.value, match.label_match)
            if key in seen:
                continue
            seen.add(key)
            yield match


def _build_alerts_query(match: AlertMatch) -> str:
    parts = [f'alertname="{_escape(match.alertname)}"', 'alertstate="firing"']
    for key, value in match.label_match:
        parts.append(f'{key}="{_escape(value)}"')
    return "ALERTS{" + ",".join(parts) + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _coalesce_intervals(
    timestamps: list[float],
    *,
    coalesce_gap_seconds: float,
) -> list[tuple[datetime, datetime]]:
    if not timestamps:
        return []
    timestamps = sorted(timestamps)
    intervals: list[tuple[datetime, datetime]] = []
    run_start = timestamps[0]
    prev = timestamps[0]
    for ts in timestamps[1:]:
        if ts - prev > coalesce_gap_seconds:
            intervals.append((_to_utc(run_start), _to_utc(prev)))
            run_start = ts
        prev = ts
    intervals.append((_to_utc(run_start), _to_utc(prev)))
    return intervals


def _to_utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)
