"""End-to-end: a pushed probe sample drives the public component status.

Exercises the full chain in a single event loop — the real
``POST /api/v1/internal/probe-results`` ingestion route (guarded by the
prober shared secret), one ``StatusEvaluator`` tick, and the
``GET /api/v1/public/components`` projection — proving that a probe pushed
by the external prober actually flips what the public status page shows.

This closes the gap where ``enable_background_tasks`` is off in tests: the
evaluator tick is driven explicitly so the assertion is deterministic, but
ingestion and the public read both go through the real HTTP surface.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx  # type: ignore[import-not-found]
from httpx import ASGITransport

from ai_horde_service_alerts.app import create_app
from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.db.session import DatabaseBundle
from ai_horde_service_alerts.db.types import ComponentStatusValue
from ai_horde_service_alerts.services.alert_mapping import AlertMapping
from ai_horde_service_alerts.services.component_loader import seed_components
from ai_horde_service_alerts.services.status_evaluator import StatusEvaluator
from ai_horde_service_alerts.settings import HordeAlertsSettings

ALERTMANAGER = "http://alertmanager.test"


async def test_probe_push_drives_public_component_status(
    settings: HordeAlertsSettings,
    database_bundle: DatabaseBundle,
    respx_mock: respx.MockRouter,
) -> None:
    # No active alerts: the pushed probe is the sole signal for "image".
    respx_mock.get(f"{ALERTMANAGER}/api/v2/alerts").mock(return_value=httpx.Response(200, json=[]))

    await seed_components(database_bundle, components_path=settings.components_config_path)
    secret = settings.prober_shared_secret.get_secret_value() if settings.prober_shared_secret else ""

    app = create_app(settings, database=database_bundle)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://service-alerts.test",
    ) as ac:
        # 1) Ingest a DOWN probe for the public "image" component via the real route.
        resp = await ac.post(
            "/api/v1/internal/probe-results",
            headers={"x-prober-secret": secret},
            json={
                "probe_name": "image_workers",
                "component_id": "image",
                "outcome": "down",
                "observed_at": datetime.now(tz=UTC).isoformat(),
                "detail": {"worker_count": 0},
            },
        )
        assert resp.status_code == 202, resp.text

        # 2) Run one evaluation tick: probe -> component_status_history.
        async with httpx.AsyncClient(base_url=ALERTMANAGER) as am_http:
            evaluator = StatusEvaluator(
                database_bundle,
                AlertmanagerClient(am_http),
                AlertMapping.from_yaml(settings.alert_component_map_path),
            )
            results = await evaluator.evaluate_once()
        assert {r.component_id: r.status for r in results}["image"] is ComponentStatusValue.DOWN

        # 3) The unauthenticated public API now reports "image" as down.
        page = await ac.get("/api/v1/public/components")
        assert page.status_code == 200, page.text
        by_id = {row["id"]: row for row in page.json()["components"]}
        assert by_id["image"]["status"] == "down"

    # A bad secret must be rejected (ingestion stays gated).
    app_again = create_app(settings, database=database_bundle)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app_again),
        base_url="http://service-alerts.test",
    ) as ac:
        denied = await ac.post(
            "/api/v1/internal/probe-results",
            headers={"x-prober-secret": "wrong-secret"},
            json={
                "probe_name": "image_workers",
                "component_id": "image",
                "outcome": "ok",
                "observed_at": datetime.now(tz=UTC).isoformat(),
            },
        )
        assert denied.status_code in (401, 403), denied.text
