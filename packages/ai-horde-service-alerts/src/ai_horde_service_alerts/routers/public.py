"""Public, unauthenticated routes returning sanitized signals."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.clients.errors import UpstreamUnavailable
from ai_horde_service_alerts.clients.mimir import MimirClient
from ai_horde_service_alerts.deps import DependencyBundle
from ai_horde_service_alerts.models.internal import MimirInstantResult
from ai_horde_service_alerts.models.public import (
    PublicIncidentsResponse,
    PublicSilenceSummary,
    ServiceStatusResponse,
)
from ai_horde_service_alerts.sanitize import (
    build_public_incidents_response,
    compute_overall_status,
    summarize_silences,
)
from ai_horde_service_alerts.settings import HordeAlertsSettings

logger = logging.getLogger(__name__)


def create_router(dependencies: DependencyBundle) -> APIRouter:
    """Create public routes with explicit dependency injection callables."""
    router = APIRouter(prefix="/api/v1/public", tags=["public"])

    SettingsDep = Annotated[HordeAlertsSettings, Depends(dependencies.get_settings)]
    AlertmanagerDep = Annotated[AlertmanagerClient, Depends(dependencies.get_alertmanager_client)]
    MimirDep = Annotated[MimirClient, Depends(dependencies.get_mimir_client)]

    @router.get("/status", response_model=ServiceStatusResponse)
    async def get_public_status(
        settings: SettingsDep,
        alertmanager: AlertmanagerDep,
        mimir: MimirDep,
    ) -> ServiceStatusResponse:
        """Return the coarse, public service-status rollup."""
        try:
            alerts = await alertmanager.list_alerts(active=True)
        except UpstreamUnavailable as exc:
            logger.warning("alertmanager unavailable for /status: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alertmanager unavailable.",
            ) from exc

        component_results: dict[str, MimirInstantResult] = {}
        if settings.mimir_curated_queries:

            async def _eval(name: str, expression: str) -> tuple[str, MimirInstantResult | None]:
                try:
                    result = await mimir.query_instant(expression)
                except (UpstreamUnavailable, ValueError) as exc:
                    logger.warning("curated query %s failed: %s", name, exc)
                    return name, None
                return name, result

            gathered = await asyncio.gather(
                *(_eval(name, query) for name, query in settings.mimir_curated_queries.items()),
            )
            for name, result in gathered:
                if result is not None:
                    component_results[name] = result

        return compute_overall_status(component_results, alerts)

    @router.get("/incidents", response_model=PublicIncidentsResponse)
    async def get_public_incidents(
        settings: SettingsDep,
        alertmanager: AlertmanagerDep,
    ) -> PublicIncidentsResponse:
        """Return active alerts projected through the public allowlist."""
        try:
            alerts = await alertmanager.list_alerts(active=True)
        except UpstreamUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alertmanager unavailable.",
            ) from exc
        return build_public_incidents_response(
            alerts,
            label_allowlist=settings.public_alert_label_allowlist,
            annotation_allowlist=settings.public_annotation_allowlist,
        )

    @router.get("/silences", response_model=PublicSilenceSummary)
    async def get_public_silences(alertmanager: AlertmanagerDep) -> PublicSilenceSummary:
        """Return an aggregate silence summary with no creator/comment attribution."""
        try:
            silences = await alertmanager.list_silences()
        except UpstreamUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alertmanager unavailable.",
            ) from exc
        return summarize_silences(silences)

    return router
