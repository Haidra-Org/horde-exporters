"""Unit tests for the curated public-stats builder."""

from __future__ import annotations

import pytest

from ai_horde_service_alerts.clients.errors import UpstreamUnavailable
from ai_horde_service_alerts.models.internal import MimirInstantResult, MimirInstantSample
from ai_horde_service_alerts.services.public_stats import PublicStatsService


class _FakeMimir:
    """Minimal stand-in exposing the one method PublicStatsService calls."""

    def __init__(self, values: dict[str, str] | None = None, *, fail: bool = False) -> None:
        self.values = values or {}
        self.fail = fail
        self.calls: list[tuple[str, str | None]] = []
        self.seen_tenants: set[str | None] = set()

    async def query_instant(self, query: str, *, tenant: str | None = None) -> MimirInstantResult:
        self.calls.append((query, tenant))
        self.seen_tenants.add(tenant)
        if self.fail:
            raise UpstreamUnavailable("mimir", "boom")
        raw = self.values.get(query)
        samples = [] if raw is None else [MimirInstantSample(metric={}, timestamp=0.0, value=raw)]
        return MimirInstantResult(result_type="vector", samples=samples)


_FULL = {
    'horde_workers_active_total{type="image"}': "42",
    'horde_workers_active_total{type="text"}': "7",
    'horde_performance_queued_requests{type="image"}': "13",
    'horde_performance_queued_requests{type="text"}': "2",
    'horde_performance_estimated_queue_drain_seconds{type="image"}': "38.5",
    'horde_performance_estimated_queue_drain_seconds{type="text"}': "4.2",
    'horde_stats_images_generated{period="day"}': "100000",
    'horde_stats_images_generated{period="month"}': "3000000",
    'horde_stats_tokens_generated{period="day"}': "55555",
}


@pytest.mark.asyncio
async def test_maps_all_fields_and_rounds_counts() -> None:
    svc = PublicStatsService(_FakeMimir(_FULL), tenant="ai-horde-public")
    stats = await svc.get_stats()
    assert stats.active_image_workers == 42
    assert stats.active_text_workers == 7
    assert stats.queued_image_requests == 13
    assert stats.queue_drain_image_seconds == pytest.approx(38.5)
    assert stats.images_generated_day == 100_000
    assert stats.images_generated_month == 3_000_000
    assert stats.tokens_generated_day == 55_555
    # No alchemy worker series exists; field stays null.
    assert stats.active_alchemy_workers is None
    assert stats.generated_at is not None


@pytest.mark.asyncio
async def test_missing_series_become_none_not_zero() -> None:
    svc = PublicStatsService(_FakeMimir({}), tenant="ai-horde-public")
    stats = await svc.get_stats()
    assert stats.active_image_workers is None
    assert stats.queued_text_requests is None
    assert stats.tokens_generated_day is None


@pytest.mark.asyncio
async def test_queries_use_the_public_tenant() -> None:
    fake = _FakeMimir(_FULL)
    await PublicStatsService(fake, tenant="ai-horde-public").get_stats()
    assert fake.seen_tenants == {"ai-horde-public"}


@pytest.mark.asyncio
async def test_ttl_cache_avoids_refetch_then_refreshes() -> None:
    fake = _FakeMimir(_FULL)
    clock = {"t": 1000.0}
    svc = PublicStatsService(fake, tenant="ai-horde-public", cache_ttl_seconds=10.0, clock=lambda: clock["t"])
    await svc.get_stats()
    n = len(fake.calls)
    clock["t"] = 1005.0  # within TTL
    await svc.get_stats()
    assert len(fake.calls) == n  # served from cache
    clock["t"] = 1011.0  # past TTL
    await svc.get_stats()
    assert len(fake.calls) == 2 * n  # refreshed


@pytest.mark.asyncio
async def test_upstream_failure_serves_last_good_then_nulls() -> None:
    fake = _FakeMimir(_FULL)
    clock = {"t": 0.0}
    svc = PublicStatsService(fake, tenant="ai-horde-public", cache_ttl_seconds=1.0, clock=lambda: clock["t"])
    good = await svc.get_stats()
    assert good.active_image_workers == 42
    # Now Mimir goes down; advance past TTL to force a refetch.
    fake.fail = True
    clock["t"] = 100.0
    after = await svc.get_stats()
    # Last-good value is retained rather than dropping to null.
    assert after.active_image_workers == 42


@pytest.mark.asyncio
async def test_upstream_failure_with_no_cache_yields_nulls() -> None:
    svc = PublicStatsService(_FakeMimir(fail=True), tenant="ai-horde-public")
    stats = await svc.get_stats()
    assert stats.active_image_workers is None
    assert stats.images_generated_day is None
