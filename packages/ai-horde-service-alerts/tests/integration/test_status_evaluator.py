"""Integration test for StatusEvaluator end-to-end against the test sqlite DB.

Verifies the worst-of-both decision rule for the curated alert
``HordeImageWorkerCountDrop`` (mapped to component ``image`` at status
``degraded`` per ``config/alert_component_map.yaml``): a single firing
alert flips the ``image`` component to ``degraded`` and writes a history
row.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import respx  # type: ignore[import-not-found]

from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.db.repositories import ComponentRepository, HistoryRepository
from ai_horde_service_alerts.db.session import DatabaseBundle
from ai_horde_service_alerts.db.types import ComponentStatusValue
from ai_horde_service_alerts.services.alert_mapping import AlertMapping
from ai_horde_service_alerts.services.component_loader import seed_components
from ai_horde_service_alerts.services.status_evaluator import StatusEvaluator
from ai_horde_service_alerts.settings import HordeAlertsSettings

ALERTMANAGER = "http://alertmanager.test"


async def test_status_evaluator_marks_image_degraded_on_curated_alert(
    settings: HordeAlertsSettings,
    database_bundle: DatabaseBundle,
    respx_mock: respx.MockRouter,
) -> None:
    await seed_components(database_bundle, components_path=settings.components_config_path)
    alert_mapping = AlertMapping.from_yaml(settings.alert_component_map_path)
    respx_mock.get(f"{ALERTMANAGER}/api/v2/alerts").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "fingerprint": "img-1",
                    "startsAt": "2025-01-01T00:00:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                    "updatedAt": "2025-01-01T00:01:00Z",
                    "status": {"state": "active"},
                    "labels": {
                        "alertname": "HordeImageWorkerCountDrop",
                        "severity": "warning",
                    },
                    "annotations": {"summary": "Image worker count dropped"},
                },
            ],
        ),
    )

    async with httpx.AsyncClient(base_url=ALERTMANAGER) as http:
        client = AlertmanagerClient(http)
        evaluator = StatusEvaluator(database_bundle, client, alert_mapping)
        results = await evaluator.evaluate_once()

    by_id = {r.component_id: r for r in results}
    assert by_id["image"].status is ComponentStatusValue.DEGRADED
    assert by_id["api"].status is ComponentStatusValue.OPERATIONAL

    async with database_bundle.session() as session:
        components_repo = ComponentRepository(session)
        history_repo = HistoryRepository(session)
        image = await components_repo.get("image")
        assert image is not None
        open_slice = await history_repo.get_open("image")
        assert open_slice is not None
        assert open_slice.status is ComponentStatusValue.DEGRADED


async def test_status_evaluator_marks_unknown_after_no_signal_grace(
    settings: HordeAlertsSettings,
    database_bundle: DatabaseBundle,
    respx_mock: respx.MockRouter,
) -> None:
    await seed_components(database_bundle, components_path=settings.components_config_path)
    alert_mapping = AlertMapping.from_yaml(settings.alert_component_map_path)
    respx_mock.get(f"{ALERTMANAGER}/api/v2/alerts").mock(return_value=httpx.Response(200, json=[]))

    async with httpx.AsyncClient(base_url=ALERTMANAGER) as http:
        client = AlertmanagerClient(http)
        evaluator = StatusEvaluator(
            database_bundle,
            client,
            alert_mapping,
            no_signal_grace=timedelta(seconds=0),
        )
        results = await evaluator.evaluate_once()

    by_id = {r.component_id: r for r in results}
    assert by_id["api"].status is ComponentStatusValue.UNKNOWN
