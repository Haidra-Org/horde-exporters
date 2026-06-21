"""Curated public-stats builder.

The public status page shows a headline strip (active workers, queue depth,
queue-drain ETA, images/tokens generated). Those numbers live in the
``ai-horde-stats-exporter`` series in the ``ai-horde-public`` Mimir tenant, but
the public API surface is otherwise metrics-free and must never accept
caller-supplied PromQL.

This module keeps a **fixed allow-list** of ``field -> PromQL`` and runs each
query against the default (public) tenant, mapping the scalar result onto a
:class:`PublicStats`. A short TTL cache shields Mimir from the public page's
request volume; on upstream failure we serve the last good payload (or nulls),
never a 5xx, so one flaky metric never takes the whole page down.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from ai_horde_service_alerts.clients.errors import UpstreamUnavailable
from ai_horde_service_alerts.models.internal import MimirInstantResult
from ai_horde_service_alerts.models.public import PublicStats

logger = logging.getLogger(__name__)


class InstantQueryClient(Protocol):
    """Structural type for the one Mimir method this service needs."""

    async def query_instant(self, query: str, *, tenant: str | None = None) -> MimirInstantResult:
        """Run an instant PromQL query and return the parsed result."""
        ...


# field name on PublicStats -> PromQL run against the public tenant.
#
# Only these queries ever reach Mimir on the public path. `active_alchemy_workers`
# is deliberately absent: the stats exporter emits no alchemy worker count, so the
# field stays null (documented on the model).
_PUBLIC_STATS_QUERIES: dict[str, str] = {
    "active_image_workers": 'horde_workers_active_total{type="image"}',
    "active_text_workers": 'horde_workers_active_total{type="text"}',
    "queued_image_requests": 'horde_performance_queued_requests{type="image"}',
    "queued_text_requests": 'horde_performance_queued_requests{type="text"}',
    "queue_drain_image_seconds": 'horde_performance_estimated_queue_drain_seconds{type="image"}',
    "queue_drain_text_seconds": 'horde_performance_estimated_queue_drain_seconds{type="text"}',
    "images_generated_day": 'horde_stats_images_generated{period="day"}',
    "images_generated_month": 'horde_stats_images_generated{period="month"}',
    "tokens_generated_day": 'horde_stats_tokens_generated{period="day"}',
}


@dataclass
class _CacheEntry:
    stats: PublicStats
    fetched_at: float


class PublicStatsService:
    """Builds and caches the public stats strip from allow-listed Mimir queries."""

    def __init__(
        self,
        mimir: InstantQueryClient,
        *,
        tenant: str,
        cache_ttl_seconds: float = 12.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Bind to a Mimir client and the tenant whose series hold public stats.

        Args:
            mimir: Anything that can run an instant Mimir query (the shared client).
            tenant: ``X-Scope-OrgID`` tenant that holds the stats-exporter series
                (the public tenant).
            cache_ttl_seconds: How long a built payload is reused before refresh.
            clock: Monotonic clock override for tests.
        """
        self._mimir = mimir
        self._tenant = tenant
        self._ttl = cache_ttl_seconds
        self._clock = clock or time.monotonic
        self._cache: _CacheEntry | None = None
        self._lock = asyncio.Lock()

    async def get_stats(self) -> PublicStats:
        """Return the public stats, refreshing from Mimir at most every TTL."""
        now = self._clock()
        cached = self._cache
        if cached is not None and (now - cached.fetched_at) < self._ttl:
            return cached.stats
        async with self._lock:
            # Re-check: another coroutine may have refreshed while we waited.
            cached = self._cache
            now = self._clock()
            if cached is not None and (now - cached.fetched_at) < self._ttl:
                return cached.stats
            stats = await self._build()
            self._cache = _CacheEntry(stats=stats, fetched_at=self._clock())
            return stats

    async def _build(self) -> PublicStats:
        q = _PUBLIC_STATS_QUERIES
        return PublicStats(
            active_image_workers=await self._int("active_image_workers", q["active_image_workers"]),
            active_text_workers=await self._int("active_text_workers", q["active_text_workers"]),
            queued_image_requests=await self._int("queued_image_requests", q["queued_image_requests"]),
            queued_text_requests=await self._int("queued_text_requests", q["queued_text_requests"]),
            queue_drain_image_seconds=await self._float("queue_drain_image_seconds", q["queue_drain_image_seconds"]),
            queue_drain_text_seconds=await self._float("queue_drain_text_seconds", q["queue_drain_text_seconds"]),
            images_generated_day=await self._int("images_generated_day", q["images_generated_day"]),
            images_generated_month=await self._int("images_generated_month", q["images_generated_month"]),
            tokens_generated_day=await self._int("tokens_generated_day", q["tokens_generated_day"]),
            generated_at=datetime.now(tz=UTC),
        )

    async def _int(self, field: str, promql: str) -> int | None:
        """Run one allow-listed query and round to an integer count."""
        value = await self._raw(field, promql)
        return None if value is None else round(value)

    async def _float(self, field: str, promql: str) -> float | None:
        """Run one allow-listed query and keep the fractional value."""
        return await self._raw(field, promql)

    async def _raw(self, field: str, promql: str) -> float | None:
        """Return the first scalar for ``promql`` or last-good/None on failure."""
        try:
            result = await self._mimir.query_instant(promql, tenant=self._tenant)
        except UpstreamUnavailable as exc:
            logger.warning("public stats query for %s failed: %s", field, exc)
            return self._last_good(field)
        return _first_scalar(result)

    def _last_good(self, field: str) -> float | None:
        """Fall back to the previously cached value for one field, else None."""
        if self._cache is None:
            return None
        value = getattr(self._cache.stats, field, None)
        return None if value is None else float(value)


def _first_scalar(result: MimirInstantResult) -> float | None:
    """Coerce the first sample of an instant result to a float, or ``None``."""
    if not result.samples:
        return None
    try:
        return float(result.samples[0].value)
    except (TypeError, ValueError):
        return None
