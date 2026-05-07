"""Internal, moderator-only routes returning raw upstream signals."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.clients.errors import UpstreamUnavailable
from ai_horde_service_alerts.clients.mimir import MimirClient
from ai_horde_service_alerts.deps import DependencyBundle
from ai_horde_service_alerts.models.internal import (
    AlertmanagerAlert,
    AlertmanagerSilence,
    AlertmanagerStatus,
    MimirInstantResult,
)

logger = logging.getLogger(__name__)


def create_router(dependencies: DependencyBundle) -> APIRouter:
    """Create moderator-only internal routes from explicit dependency callables."""
    router = APIRouter(
        prefix="/api/v1/internal",
        tags=["internal"],
        dependencies=[Depends(dependencies.require_moderator)],
    )

    AlertmanagerDep = Annotated[AlertmanagerClient, Depends(dependencies.get_alertmanager_client)]
    MimirDep = Annotated[MimirClient, Depends(dependencies.get_mimir_client)]

    @router.get("/alerts", response_model=list[AlertmanagerAlert])
    async def list_internal_alerts(
        alertmanager: AlertmanagerDep,
        active: bool = True,
        silenced: bool = False,
        inhibited: bool = False,
    ) -> list[AlertmanagerAlert]:
        """Return raw, unsanitized Alertmanager alerts."""
        try:
            return await alertmanager.list_alerts(
                active=active,
                silenced=silenced,
                inhibited=inhibited,
            )
        except UpstreamUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alertmanager unavailable.",
            ) from exc

    @router.get("/silences", response_model=list[AlertmanagerSilence])
    async def list_internal_silences(
        alertmanager: AlertmanagerDep,
    ) -> list[AlertmanagerSilence]:
        """Return raw, unsanitized Alertmanager silences."""
        try:
            return await alertmanager.list_silences()
        except UpstreamUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alertmanager unavailable.",
            ) from exc

    @router.get("/status/alertmanager", response_model=AlertmanagerStatus)
    async def get_internal_alertmanager_status(
        alertmanager: AlertmanagerDep,
    ) -> AlertmanagerStatus:
        """Return raw Alertmanager `/api/v2/status` payload."""
        try:
            return await alertmanager.get_status()
        except UpstreamUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alertmanager unavailable.",
            ) from exc

    @router.get("/metrics/instant", response_model=MimirInstantResult)
    async def get_internal_metrics_instant(
        mimir: MimirDep,
        query: Annotated[str, Query(min_length=1, max_length=4096)],
        tenant: Annotated[str | None, Query()] = None,
    ) -> MimirInstantResult:
        """Run an instant PromQL query against Mimir and return the parsed result."""
        try:
            return await mimir.query_instant(query, tenant=tenant)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except UpstreamUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Mimir unavailable.",
            ) from exc

    return router
