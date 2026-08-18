"""FastAPI application factory and lifespan."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_horde_service_alerts.auth import ModeratorAuthGuard
from ai_horde_service_alerts.backfill import run_backfill
from ai_horde_service_alerts.clients.aihorde import AiHordeClient
from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.clients.mimir import MimirClient
from ai_horde_service_alerts.db.session import DatabaseBundle, build_database_bundle
from ai_horde_service_alerts.deps import build_dependency_bundle
from ai_horde_service_alerts.routers import health as health_router
from ai_horde_service_alerts.routers import internal as internal_router
from ai_horde_service_alerts.routers import public as public_router
from ai_horde_service_alerts.services.alert_mapping import AlertMapping
from ai_horde_service_alerts.services.component_loader import seed_components
from ai_horde_service_alerts.services.maintenance_runner import MaintenanceRunner
from ai_horde_service_alerts.services.status_evaluator import StatusEvaluator
from ai_horde_service_alerts.settings import HordeAlertsSettings, get_settings

logger = logging.getLogger(__name__)


def create_app(
    settings: HordeAlertsSettings | None = None,
    *,
    database: DatabaseBundle | None = None,
) -> FastAPI:
    """Build the FastAPI application using ``settings`` or the cached default.

    ``database`` can be supplied by tests to bind the app to a pre-built
    (e.g. in-memory SQLite) bundle without the lifespan creating its own.
    """
    resolved_settings = settings or get_settings()
    logger.info("Creating FastAPI app with settings: %s", resolved_settings)

    timeout = httpx.Timeout(resolved_settings.request_timeout_seconds)
    upstream_auth: httpx.BasicAuth | None = None
    if resolved_settings.upstream_basic_auth_user and resolved_settings.upstream_basic_auth_password is not None:
        upstream_auth = httpx.BasicAuth(
            resolved_settings.upstream_basic_auth_user,
            resolved_settings.upstream_basic_auth_password.get_secret_value(),
        )

    alertmanager_http = httpx.AsyncClient(
        base_url=str(resolved_settings.alertmanager_base_url),
        timeout=timeout,
        auth=upstream_auth,
    )
    mimir_http = httpx.AsyncClient(
        base_url=str(resolved_settings.mimir_base_url),
        timeout=timeout,
        auth=upstream_auth,
    )
    aihorde_http = httpx.AsyncClient(
        base_url=str(resolved_settings.aihorde_base_url),
        timeout=timeout,
    )

    aihorde_client = AiHordeClient(
        aihorde_http,
        client_agent=resolved_settings.aihorde_client_agent,
    )

    owns_database = False
    db_bundle: DatabaseBundle | None = database
    if db_bundle is None and resolved_settings.enable_db:
        db_bundle = build_database_bundle(resolved_settings)
        owns_database = True

    alert_mapping = AlertMapping.from_yaml(resolved_settings.alert_component_map_path)
    alertmanager_client = AlertmanagerClient(alertmanager_http)
    mimir_client = MimirClient(
        mimir_http,
        default_tenant=resolved_settings.mimir_tenant_default,
    )
    auth_guard = ModeratorAuthGuard(
        aihorde_client,
        positive_ttl_seconds=resolved_settings.moderator_cache_ttl_seconds,
        negative_ttl_seconds=resolved_settings.moderator_cache_negative_ttl_seconds,
        max_entries=resolved_settings.moderator_cache_max_entries,
    )
    dependencies = build_dependency_bundle(
        settings=resolved_settings,
        alertmanager_client=alertmanager_client,
        mimir_client=mimir_client,
        auth_guard=auth_guard,
        database=db_bundle,
        alert_mapping=alert_mapping,
    )

    background_tasks: list[asyncio.Task[None]] = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        if db_bundle is not None:
            try:
                await seed_components(
                    db_bundle,
                    components_path=resolved_settings.components_config_path,
                )
            except Exception:  # pragma: no cover - operator-visible at boot only
                logger.exception("component seeding failed; continuing with whatever is in the DB")

        if db_bundle is not None and resolved_settings.backfill_on_startup:
            try:
                await run_backfill(
                    database=db_bundle,
                    mimir=mimir_client,
                    alert_mapping=alert_mapping,
                    window_days=resolved_settings.backfill_window_days,
                )
            except Exception:  # pragma: no cover - boot-time observability
                logger.exception("backfill failed; service will continue without historical fill")

        if db_bundle is not None and resolved_settings.enable_background_tasks:
            runner = MaintenanceRunner(
                db_bundle,
                probe_result_retention=timedelta(days=resolved_settings.probe_result_retention_days),
            )
            evaluator = StatusEvaluator(
                db_bundle,
                alertmanager_client,
                alert_mapping,
                no_signal_grace=timedelta(seconds=resolved_settings.no_signal_grace_seconds),
                flap_confirmations=resolved_settings.status_flap_confirmations,
            )
            background_tasks.append(
                asyncio.create_task(
                    _periodic(
                        evaluator.evaluate_once,
                        interval=resolved_settings.status_evaluator_interval_seconds,
                        name="status_evaluator",
                    ),
                ),
            )
            background_tasks.append(
                asyncio.create_task(
                    _periodic(
                        runner.tick,
                        interval=resolved_settings.maintenance_runner_interval_seconds,
                        name="maintenance_runner",
                    ),
                ),
            )

        try:
            yield
        finally:
            for task in background_tasks:
                task.cancel()
            for task in background_tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await task
            await alertmanager_http.aclose()
            await mimir_http.aclose()
            await aihorde_http.aclose()
            if owns_database and db_bundle is not None:
                await db_bundle.dispose()

    # Docs/OpenAPI live under /api to match the API routes (/api/v1/*); only the
    # liveness/readiness probes (/healthz, /readyz) stay at the root.
    docs_url = "/api/docs" if resolved_settings.enable_internal_swagger_docs else None
    redoc_url = "/api/redoc" if resolved_settings.enable_internal_swagger_docs else None
    openapi_url = "/api/openapi.json" if resolved_settings.enable_internal_swagger_docs else None

    app = FastAPI(
        title="AI Horde Service Alerts",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )

    if resolved_settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved_settings.cors_allow_origins),
            allow_methods=["GET"],
            allow_headers=["apikey", "content-type"],
            allow_credentials=False,
        )

    app.include_router(health_router.create_router(dependencies))
    app.include_router(public_router.create_router(dependencies))
    app.include_router(internal_router.create_probe_router(dependencies))
    app.include_router(internal_router.create_router(dependencies))
    return app


async def _periodic[PeriodicResultT](
    coro_factory: Callable[[], Awaitable[PeriodicResultT]],
    *,
    interval: float,
    name: str,
) -> None:
    """Run an async no-arg callable on a fixed interval until cancelled."""
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("background task %s failed; will retry next tick", name)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
