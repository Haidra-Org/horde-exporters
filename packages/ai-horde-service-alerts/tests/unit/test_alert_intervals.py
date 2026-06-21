"""Unit tests for the shared ALERTS interval-coalescing helper."""

from __future__ import annotations

from ai_horde_service_alerts.services.alert_intervals import coalesce_intervals, to_utc


def test_empty_returns_no_intervals() -> None:
    assert coalesce_intervals([], coalesce_gap_seconds=120.0) == []


def test_contiguous_samples_form_one_interval() -> None:
    ts = [0.0, 60.0, 120.0, 180.0]
    intervals = coalesce_intervals(ts, coalesce_gap_seconds=120.0)
    assert len(intervals) == 1
    start, end = intervals[0]
    assert start == to_utc(0.0)
    assert end == to_utc(180.0)


def test_gap_larger_than_threshold_splits() -> None:
    # 0..120 firing, big gap, then 1000..1060 firing again.
    ts = [0.0, 60.0, 120.0, 1000.0, 1060.0]
    intervals = coalesce_intervals(ts, coalesce_gap_seconds=120.0)
    assert len(intervals) == 2
    assert intervals[0] == (to_utc(0.0), to_utc(120.0))
    assert intervals[1] == (to_utc(1000.0), to_utc(1060.0))


def test_unsorted_input_is_sorted_first() -> None:
    ts = [120.0, 0.0, 60.0]
    intervals = coalesce_intervals(ts, coalesce_gap_seconds=120.0)
    assert intervals == [(to_utc(0.0), to_utc(120.0))]


def test_single_sample_is_zero_length_interval() -> None:
    intervals = coalesce_intervals([500.0], coalesce_gap_seconds=120.0)
    assert intervals == [(to_utc(500.0), to_utc(500.0))]
