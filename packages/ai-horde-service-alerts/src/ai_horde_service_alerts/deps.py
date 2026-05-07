"""Reusable FastAPI dependency factories for runtime service objects."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from ai_horde_service_alerts.auth import ModeratorAuthGuard, ModeratorIdentity
from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.clients.mimir import MimirClient
from ai_horde_service_alerts.settings import HordeAlertsSettings

AIHORDE_API_KEY_HEADER = APIKeyHeader(
    name="apikey",
    scheme_name="AiHordeApiKey",
    description="AI Horde API key used to authorize moderator-only endpoints.",
    auto_error=False,
)

@dataclass(frozen=True, slots=True)
class DependencyBundle:
    """Holds dependency callables bound to one FastAPI app instance."""

    get_settings: Callable[[], HordeAlertsSettings]
    get_alertmanager_client: Callable[[], AlertmanagerClient]
    get_mimir_client: Callable[[], MimirClient]
    get_auth_guard: Callable[[], ModeratorAuthGuard]
    require_moderator: Callable[..., Awaitable[ModeratorIdentity]]


def build_dependency_bundle(
    *,
    settings: HordeAlertsSettings,
    alertmanager_client: AlertmanagerClient,
    mimir_client: MimirClient,
    auth_guard: ModeratorAuthGuard,
) -> DependencyBundle:
    """Create app-scoped dependency callables from concrete service instances."""

    def get_settings_dep() -> HordeAlertsSettings:
        """Return immutable service settings for dependency injection."""
        return settings

    def get_alertmanager_client_dep() -> AlertmanagerClient:
        """Return the shared Alertmanager client for the current app."""
        return alertmanager_client

    def get_mimir_client_dep() -> MimirClient:
        """Return the shared Mimir client for the current app."""
        return mimir_client

    def get_auth_guard_dep() -> ModeratorAuthGuard:
        """Return the shared moderator authentication guard."""
        return auth_guard

    async def require_moderator_dep(
        guard: Annotated[ModeratorAuthGuard, Depends(get_auth_guard_dep)],
        api_key: Annotated[str | None, Security(AIHORDE_API_KEY_HEADER)],
    ) -> ModeratorIdentity:
        """Enforce moderator-only access via the ``apikey`` request header."""
        return await guard.authenticate(api_key)

    return DependencyBundle(
        get_settings=get_settings_dep,
        get_alertmanager_client=get_alertmanager_client_dep,
        get_mimir_client=get_mimir_client_dep,
        get_auth_guard=get_auth_guard_dep,
        require_moderator=require_moderator_dep,
    )
