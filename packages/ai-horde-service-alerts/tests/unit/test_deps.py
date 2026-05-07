"""Unit tests for FastAPI dependency bundle construction."""

from __future__ import annotations

from typing import cast

import httpx

from ai_horde_service_alerts.auth import ModeratorAuthGuard, ModeratorIdentity
from ai_horde_service_alerts.clients.aihorde import AiHordeClient
from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.clients.mimir import MimirClient
from ai_horde_service_alerts.deps import build_dependency_bundle
from ai_horde_service_alerts.settings import HordeAlertsSettings


class _GuardStub:
    """Small async guard stub used to validate dependency delegation."""

    def __init__(self, identity: ModeratorIdentity) -> None:
        self._identity = identity
        self.calls: list[str | None] = []

    async def authenticate(self, api_key: str | None) -> ModeratorIdentity:
        self.calls.append(api_key)
        return self._identity


async def test_bundle_returns_bound_singleton_instances(
    settings: HordeAlertsSettings,
) -> None:
    alertmanager_http = httpx.AsyncClient(base_url=str(settings.alertmanager_base_url))
    mimir_http = httpx.AsyncClient(base_url=str(settings.mimir_base_url))
    aihorde_http = httpx.AsyncClient(base_url=str(settings.aihorde_base_url))

    alertmanager_client = AlertmanagerClient(alertmanager_http)
    mimir_client = MimirClient(mimir_http, default_tenant=settings.mimir_tenant_default)
    auth_guard = ModeratorAuthGuard(
        AiHordeClient(aihorde_http, client_agent=settings.aihorde_client_agent),
        positive_ttl_seconds=settings.moderator_cache_ttl_seconds,
        negative_ttl_seconds=settings.moderator_cache_negative_ttl_seconds,
        max_entries=settings.moderator_cache_max_entries,
    )

    bundle = build_dependency_bundle(
        settings=settings,
        alertmanager_client=alertmanager_client,
        mimir_client=mimir_client,
        auth_guard=auth_guard,
    )

    try:
        assert bundle.get_settings() is settings
        assert bundle.get_alertmanager_client() is alertmanager_client
        assert bundle.get_mimir_client() is mimir_client
        assert bundle.get_auth_guard() is auth_guard
    finally:
        await alertmanager_http.aclose()
        await mimir_http.aclose()
        await aihorde_http.aclose()


async def test_require_moderator_dependency_delegates_to_guard(
    settings: HordeAlertsSettings,
) -> None:
    alertmanager_http = httpx.AsyncClient(base_url=str(settings.alertmanager_base_url))
    mimir_http = httpx.AsyncClient(base_url=str(settings.mimir_base_url))
    identity = ModeratorIdentity(username="mod", user_id=123)
    guard_stub = cast(ModeratorAuthGuard, _GuardStub(identity))

    bundle = build_dependency_bundle(
        settings=settings,
        alertmanager_client=AlertmanagerClient(alertmanager_http),
        mimir_client=MimirClient(mimir_http, default_tenant=settings.mimir_tenant_default),
        auth_guard=guard_stub,
    )

    try:
        result = await bundle.require_moderator(
            guard=bundle.get_auth_guard(),
            apikey="moderator-key",
        )
        assert result == identity
        stub = cast(_GuardStub, bundle.get_auth_guard())
        assert stub.calls == ["moderator-key"]
    finally:
        await alertmanager_http.aclose()
        await mimir_http.aclose()
