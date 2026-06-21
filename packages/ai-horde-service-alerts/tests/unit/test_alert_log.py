"""Unit tests for reconstructing the firing/resolved alert log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_horde_service_alerts.models.internal import MimirRangeResult, MimirRangeSeries
from ai_horde_service_alerts.services.alert_log import entries_from_range

NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)
STEP = 60.0
GAP = STEP * 2.0


def _series(labels: dict[str, str], start: datetime, samples: int) -> MimirRangeSeries:
    values = [((start + timedelta(seconds=STEP * i)).timestamp(), "1") for i in range(samples)]
    return MimirRangeSeries(metric=labels, values=values)


def test_recent_interval_is_firing_with_null_end() -> None:
    # Samples right up to NOW => still firing.
    series = _series(
        {"alertname": "HordeImageQueueBacklog", "severity": "warning", "component": "image"},
        start=NOW - timedelta(minutes=10),
        samples=11,  # last sample == NOW
    )
    [entry] = entries_from_range(
        MimirRangeResult(series=[series]),
        now=NOW,
        coalesce_gap_seconds=GAP,
    )
    assert entry.state == "firing"
    assert entry.ended_at is None
    assert entry.component == "image"
    assert entry.severity == "warning"
    assert entry.for_seconds == 10 * 60


def test_old_interval_is_resolved_with_end() -> None:
    # An interval that ended well before NOW => resolved.
    start = NOW - timedelta(hours=3)
    series = _series({"alertname": "HordeAPIDown"}, start=start, samples=6)  # spans 5 minutes
    [entry] = entries_from_range(
        MimirRangeResult(series=[series]),
        now=NOW,
        coalesce_gap_seconds=GAP,
    )
    assert entry.state == "resolved"
    assert entry.ended_at is not None
    assert entry.for_seconds == 5 * 60


def test_series_without_alertname_is_skipped() -> None:
    series = MimirRangeSeries(metric={"severity": "warning"}, values=[(NOW.timestamp(), "1")])
    assert entries_from_range(MimirRangeResult(series=[series]), now=NOW, coalesce_gap_seconds=GAP) == []


def test_entries_sorted_by_start_descending() -> None:
    older = _series({"alertname": "Old"}, start=NOW - timedelta(hours=5), samples=3)
    newer = _series({"alertname": "New"}, start=NOW - timedelta(hours=1), samples=3)
    entries = entries_from_range(
        MimirRangeResult(series=[older, newer]),
        now=NOW,
        coalesce_gap_seconds=GAP,
    )
    assert [e.alertname for e in entries] == ["New", "Old"]
