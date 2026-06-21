"""Reconstruct firing intervals from Prometheus ``ALERTS`` samples.

Mimir keeps an ``ALERTS{alertstate="firing"}`` series for the lifetime of a
firing alert. A range query returns one sample per evaluation step while the
alert is firing and simply stops once it resolves. Both the one-shot history
backfill (:mod:`ai_horde_service_alerts.backfill`) and the operator alert-log
endpoint need to turn that sparse sample stream back into ``[start, end]``
intervals, so the coalescing logic lives here and is shared by both.
"""

from __future__ import annotations

from datetime import UTC, datetime


def coalesce_intervals(
    timestamps: list[float],
    *,
    coalesce_gap_seconds: float,
) -> list[tuple[datetime, datetime]]:
    """Group sorted-or-unsorted unix timestamps into contiguous firing intervals.

    Consecutive samples no more than ``coalesce_gap_seconds`` apart belong to the
    same interval; a larger gap starts a new one. Returned bounds are timezone-aware
    UTC datetimes.
    """
    if not timestamps:
        return []
    ordered = sorted(timestamps)
    intervals: list[tuple[datetime, datetime]] = []
    run_start = ordered[0]
    prev = ordered[0]
    for ts in ordered[1:]:
        if ts - prev > coalesce_gap_seconds:
            intervals.append((to_utc(run_start), to_utc(prev)))
            run_start = ts
        prev = ts
    intervals.append((to_utc(run_start), to_utc(prev)))
    return intervals


def to_utc(ts: float) -> datetime:
    """Convert a unix timestamp (seconds) to a timezone-aware UTC datetime."""
    return datetime.fromtimestamp(ts, tz=UTC)
