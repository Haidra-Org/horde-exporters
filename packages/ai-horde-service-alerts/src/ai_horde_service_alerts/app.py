"""FastAPI application factory and lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_horde_service_alerts.auth import ModeratorAuthGuard
from ai_horde_service_alerts.clients.aihorde import AiHordeClient
from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.clients.mimir import MimirClient
from ai_horde_service_alerts.deps import build_dependency_bundle
from ai_horde_service_alerts.routers import health as health_router
from ai_horde_service_alerts.routers import internal as internal_router
from ai_horde_service_alerts.routers import public as public_router
from ai_horde_service_alerts.settings import HordeAlertsSettings, get_settings

logger = logging.getLogger(__name__)


def create_app(settings: HordeAlertsSettings | None = None) -> FastAPI:
    """Build the FastAPI application using ``settings`` or the cached default."""
    logger.info("Creating FastAPI app with settings: %s", settings or get_settings())
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
    dependencies = build_dependency_bundle(
        settings=resolved_settings,
        alertmanager_client=AlertmanagerClient(alertmanager_http),
        mimir_client=MimirClient(
            mimir_http,
            default_tenant=resolved_settings.mimir_tenant_default,
        ),
        auth_guard=ModeratorAuthGuard(
            aihorde_client,
            positive_ttl_seconds=resolved_settings.moderator_cache_ttl_seconds,
            negative_ttl_seconds=resolved_settings.moderator_cache_negative_ttl_seconds,
            max_entries=resolved_settings.moderator_cache_max_entries,
        ),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        try:
            yield
        finally:
            await alertmanager_http.aclose()
            await mimir_http.aclose()
            await aihorde_http.aclose()

    docs_url = "/docs" if resolved_settings.enable_internal_swagger_docs else None
    redoc_url = "/redoc" if resolved_settings.enable_internal_swagger_docs else None
    openapi_url = "/openapi.json" if resolved_settings.enable_internal_swagger_docs else None

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
    app.include_router(internal_router.create_router(dependencies))
    return app
