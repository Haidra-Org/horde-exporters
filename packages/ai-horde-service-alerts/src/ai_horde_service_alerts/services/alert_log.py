"""Reconstruct a recent firing/resolved alert log from Mimir ``ALERTS``.

``/internal/alerts/summary`` only reports alerts that are firing *right now*.
The operator UI also wants a short backward-looking log that includes alerts
which have since resolved. Mimir holds that history: ``ALERTS{alertstate="firing"}``
has a sample per evaluation step for the lifetime of each firing alert. We range
query it over the requested window, coalesce each series' samples into intervals
(shared with the history backfill), and label intervals that ran up to ~now as
still firing and older ones as resolved.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from ai_horde_service_alerts.clients.mimir import MimirClient
from ai_horde_service_alerts.models.internal import AdminAlertLogEntry, MimirRangeResult
from ai_horde_service_alerts.services.alert_intervals import coalesce_intervals

logger = logging.getLogger(__name__)

_ALERTS_FIRING_QUERY = 'ALERTS{alertstate="firing"}'


async def build_alert_log(
    mimir: MimirClient,
    *,
    hours: int,
    tenant: str | None = None,
    step_seconds: float = 60.0,
    now: datetime | None = None,
) -> list[AdminAlertLogEntry]:
    """Return firing + resolved alert intervals over the trailing ``hours`` window.

    Args:
        mimir: Mimir client used for the range query.
        hours: How far back to look.
        tenant: Tenant override; defaults to the client's default tenant (the
            tenant that holds the ``ALERTS`` series).
        step_seconds: Range-query step; also sets the coalescing gap (2x).
        now: Override "now" for tests.

    Raises:
        UpstreamUnavailable: When the Mimir range query fails.
    """
    moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
    start_dt = moment - timedelta(hours=hours)
    result = await mimir.query_range(
        _ALERTS_FIRING_QUERY,
        start=start_dt.timestamp(),
        end=moment.timestamp(),
        step=step_seconds,
        tenant=tenant,
    )
    return entries_from_range(result, now=moment, coalesce_gap_seconds=step_seconds * 2.0)


def entries_from_range(
    result: MimirRangeResult,
    *,
    now: datetime,
    coalesce_gap_seconds: float,
) -> list[AdminAlertLogEntry]:
    """Turn a parsed ``ALERTS`` range result into sorted log entries (pure)."""
    # An interval whose last sample is within one coalesce gap of "now" is treated
    # as still firing; anything older has resolved.
    still_firing_cutoff = now - timedelta(seconds=coalesce_gap_seconds)
    entries: list[AdminAlertLogEntry] = []
    for series in result.series:
        labels = series.metric
        alertname = labels.get("alertname")
        if not alertname:
            continue
        timestamps = [ts for ts, _ in series.values]
        for interval_start, interval_end in coalesce_intervals(
            timestamps,
            coalesce_gap_seconds=coalesce_gap_seconds,
        ):
            firing = interval_end >= still_firing_cutoff
            ended_at = None if firing else interval_end
            span_end = now if firing else interval_end
            for_seconds = max(int((span_end - interval_start).total_seconds()), 0)
            entries.append(
                AdminAlertLogEntry(
                    alertname=alertname,
                    severity=labels.get("severity"),
                    component=labels.get("component"),
                    state="firing" if firing else "resolved",
                    started_at=interval_start,
                    ended_at=ended_at,
                    for_seconds=for_seconds,
                ),
            )
    entries.sort(key=lambda e: e.started_at, reverse=True)
    return entries
