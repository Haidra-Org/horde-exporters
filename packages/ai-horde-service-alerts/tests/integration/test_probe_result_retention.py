"""probe_results must stay cheap to read and bounded in size.

Regression coverage for the evaluator pegging a core: ``latest_per_component``
used to select the entire table every tick and filter in Python, and nothing
ever pruned the table. These tests pin the bounded query's semantics and the
runner's pruning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from ai_horde_service_alerts.db.models import ProbeResult
from ai_horde_service_alerts.db.repositories import ProbeResultRepository
from ai_horde_service_alerts.db.session import DatabaseBundle
from ai_horde_service_alerts.db.types import ProbeOutcome
from ai_horde_service_alerts.services.component_loader import seed_components
from ai_horde_service_alerts.services.maintenance_runner import MaintenanceRunner
from ai_horde_service_alerts.settings import HordeAlertsSettings

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


async def _seed_samples(database_bundle: DatabaseBundle, *, per_component: int, components: tuple[str, ...]) -> None:
    async with database_bundle.session() as session:
        repo = ProbeResultRepository(session)
        for component_id in components:
            for i in range(per_component):
                # Oldest first; the newest sample for each component is DOWN,
                # everything older is OK, so a wrong "latest" pick is visible.
                age = timedelta(minutes=per_component - i)
                await repo.record(
                    probe_name=f"probe-{component_id}",
                    component_id=component_id,
                    outcome=ProbeOutcome.DOWN if i == per_component - 1 else ProbeOutcome.OK,
                    observed_at=NOW - age,
                    latency_ms=i,
                )


async def test_latest_per_component_returns_newest_row_per_component(
    settings: HordeAlertsSettings,
    database_bundle: DatabaseBundle,
) -> None:
    await seed_components(database_bundle, components_path=settings.components_config_path)
    await _seed_samples(database_bundle, per_component=50, components=("image", "text"))

    async with database_bundle.session() as session:
        latest = await ProbeResultRepository(session).latest_per_component(now=NOW)

    assert set(latest) == {"image", "text"}
    for row in latest.values():
        assert row.outcome == ProbeOutcome.DOWN
        assert row.observed_at == NOW - timedelta(minutes=1)


async def test_latest_per_component_applies_freshness_in_query(
    settings: HordeAlertsSettings,
    database_bundle: DatabaseBundle,
) -> None:
    await seed_components(database_bundle, components_path=settings.components_config_path)
    await _seed_samples(database_bundle, per_component=50, components=("image", "text"))

    async with database_bundle.session() as session:
        repo = ProbeResultRepository(session)
        # Newest sample is 1 minute old: a 30 s freshness window excludes everything,
        # and must not fall back to a stale-but-newest row.
        assert await repo.latest_per_component(freshness=timedelta(seconds=30), now=NOW) == {}
        fresh = await repo.latest_per_component(freshness=timedelta(minutes=5), now=NOW)
        assert set(fresh) == {"image", "text"}
        assert all(row.outcome == ProbeOutcome.DOWN for row in fresh.values())


async def test_maintenance_runner_prunes_expired_probe_results(
    settings: HordeAlertsSettings,
    database_bundle: DatabaseBundle,
) -> None:
    await seed_components(database_bundle, components_path=settings.components_config_path)
    # 200 samples per component spanning 200 minutes; keep the last hour.
    await _seed_samples(database_bundle, per_component=200, components=("image", "text"))

    runner = MaintenanceRunner(database_bundle, probe_result_retention=timedelta(hours=1))
    await runner.tick(now=NOW)

    async with database_bundle.session() as session:
        remaining = (await session.execute(select(func.count()).select_from(ProbeResult))).scalar_one()
        oldest = (await session.execute(select(func.min(ProbeResult.observed_at)))).scalar_one()
        latest = await ProbeResultRepository(session).latest_per_component(now=NOW)

    # ages 1..60 minutes survive for each of the two components
    assert remaining == 120
    assert oldest is not None and oldest >= NOW - timedelta(hours=1)
    assert all(row.outcome == ProbeOutcome.DOWN for row in latest.values())


async def test_maintenance_runner_pruning_can_be_disabled(
    settings: HordeAlertsSettings,
    database_bundle: DatabaseBundle,
) -> None:
    await seed_components(database_bundle, components_path=settings.components_config_path)
    await _seed_samples(database_bundle, per_component=10, components=("image",))

    await MaintenanceRunner(database_bundle, probe_result_retention=None).tick(now=NOW)

    async with database_bundle.session() as session:
        remaining = (await session.execute(select(func.count()).select_from(ProbeResult))).scalar_one()
    assert remaining == 10
