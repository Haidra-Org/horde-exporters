"""Integration test for the Mimir → component_status_history backfill.

Asserts that:

1. ``run_backfill`` walks per-series timestamps, coalesces into firing
   intervals using a ``2 * step`` gap threshold, and writes one
   ``ComponentStatusHistory`` row per interval at
   ``source = HistorySource.BACKFILL`` with the curated status.
2. Re-running it on the same window is a no-op (every interval already
   present is reported as ``intervals_skipped`` and the row count does
   not grow).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import respx  # type: ignore[import-not-found]
from sqlalchemy import func, select

from ai_horde_service_alerts.backfill import run_backfill
from ai_horde_service_alerts.clients.mimir import MimirClient
from ai_horde_service_alerts.db.models import ComponentStatusHistory
from ai_horde_service_alerts.db.session import DatabaseBundle
from ai_horde_service_alerts.db.types import HistorySource
from ai_horde_service_alerts.services.alert_mapping import AlertMapping
from ai_horde_service_alerts.services.component_loader import seed_components
from ai_horde_service_alerts.settings import HordeAlertsSettings

MIMIR = "http://mimir.test"


async def test_backfill_writes_intervals_and_is_idempotent(
    settings: HordeAlertsSettings,
    database_bundle: DatabaseBundle,
    respx_mock: respx.MockRouter,
) -> None:
    await seed_components(database_bundle, components_path=settings.components_config_path)
    alert_mapping = AlertMapping.from_yaml(settings.alert_component_map_path)

    base = datetime(2025, 1, 10, 12, 0, 0, tzinfo=UTC)
    step = 60.0
    interval_a = [base + timedelta(seconds=step * i) for i in range(5)]
    interval_b = [base + timedelta(seconds=step * i) for i in range(20, 23)]
    series_values = [[t.timestamp(), "1"] for t in interval_a + interval_b]

    def _range_handler(request: httpx.Request) -> httpx.Response:
        promql = request.url.params.get("query", "")
        if "HordeImageWorkerCountDrop" in promql:
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [
                            {
                                "metric": {
                                    "alertname": "HordeImageWorkerCountDrop",
                                    "alertstate": "firing",
                                },
                                "values": series_values,
                            },
                        ],
                    },
                },
            )
        return httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "matrix", "result": []}},
        )

    respx_mock.get(f"{MIMIR}/prometheus/api/v1/query_range").mock(side_effect=_range_handler)

    async with httpx.AsyncClient(base_url=MIMIR) as http:
        mimir = MimirClient(http, default_tenant=settings.mimir_tenant_default)

        first = await run_backfill(
            database=database_bundle,
            mimir=mimir,
            alert_mapping=alert_mapping,
            window_days=30,
            step_seconds=step,
            now=base + timedelta(hours=1),
        )
        assert first.intervals_inserted == 2
        assert first.intervals_skipped == 0

        second = await run_backfill(
            database=database_bundle,
            mimir=mimir,
            alert_mapping=alert_mapping,
            window_days=30,
            step_seconds=step,
            now=base + timedelta(hours=1),
        )
        assert second.intervals_inserted == 0
        assert second.intervals_skipped == 2

    async with database_bundle.session() as session:
        total = await session.execute(
            select(func.count())
            .select_from(ComponentStatusHistory)
            .where(
                ComponentStatusHistory.component_id == "image",
                ComponentStatusHistory.source == HistorySource.BACKFILL,
            ),
        )
        assert total.scalar_one() == 2
