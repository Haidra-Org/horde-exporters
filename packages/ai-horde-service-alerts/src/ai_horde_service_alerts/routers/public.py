"""Public, unauthenticated routes serving the redesigned status page.

The public surface is **structural-only**:

* ``GET /public/components`` — current pill + 90-day uptime % per component.
* ``GET /public/incidents`` — operator-authored incidents and their timelines.
* ``GET /public/maintenance`` — upcoming + active maintenance windows.
* ``GET /public/history`` — daily uptime buckets for one component.

Original Alertmanager ``summary`` annotations are never copied into any
response body — incident prose is whatever an operator wrote.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_horde_service_alerts.db.repositories import (
    ComponentRepository,
    HistoryRepository,
    MaintenanceRepository,
)
from ai_horde_service_alerts.db.repositories.incidents import IncidentRepository
from ai_horde_service_alerts.db.repositories.overrides import OverrideRepository
from ai_horde_service_alerts.db.types import Audience, ComponentStatusValue, worst_status
from ai_horde_service_alerts.deps import DependencyBundle
from ai_horde_service_alerts.models.public import (
    PublicComponent,
    PublicComponentsResponse,
    PublicHistoryResponse,
    PublicIncidentsResponse,
    PublicMaintenanceResponse,
    PublicStats,
)
from ai_horde_service_alerts.services.projections import (
    history_response,
    public_component,
    public_incident_from_row,
    public_maintenance_from_row,
)
from ai_horde_service_alerts.services.public_stats import PublicStatsService

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def create_router(dependencies: DependencyBundle) -> APIRouter:
    """Create the public router using the supplied dependency bundle."""
    router = APIRouter(prefix="/api/v1/public", tags=["public"])

    SessionDep = Annotated[AsyncSession, Depends(dependencies.get_session)]

    # One service per app so its TTL cache is shared across requests. The Mimir
    # client and default (public) tenant are app-scoped, so it is safe to build
    # the service eagerly here at router-construction time.
    settings = dependencies.get_settings()
    stats_service = PublicStatsService(
        dependencies.get_mimir_client(),
        tenant=settings.mimir_tenant_default,
    )

    @router.get("/components", response_model=PublicComponentsResponse)
    async def get_public_components(session: SessionDep) -> PublicComponentsResponse:
        """Return current public components with status, uptime% and last change time."""
        components_repo = ComponentRepository(session)
        history_repo = HistoryRepository(session)
        override_repo = OverrideRepository(session)

        components = await components_repo.list_all(audience=Audience.PUBLIC)
        rows: list[PublicComponent] = []
        statuses: list[ComponentStatusValue] = []
        moment = _now()
        for component in components:
            open_slice = await history_repo.get_open(component.id)
            override = await override_repo.get_active(component.id, now=moment)
            status_value = (
                override.target_status
                if override is not None
                else (open_slice.status if open_slice else ComponentStatusValue.UNKNOWN)
            )
            uptime = await history_repo.uptime_percent(component.id, days=90, now=moment)
            last_change = open_slice.started_at if open_slice else None
            rows.append(
                public_component(
                    component=component,
                    status=status_value,
                    last_change_at=last_change,
                    uptime_90d=uptime,
                ),
            )
            statuses.append(status_value)

        overall = worst_status(*statuses) if statuses else ComponentStatusValue.UNKNOWN
        return PublicComponentsResponse(components=rows, overall=overall, generated_at=moment)

    @router.get("/incidents", response_model=PublicIncidentsResponse)
    async def get_public_incidents(
        session: SessionDep,
        include_resolved_count: Annotated[int, Query(ge=0, le=20)] = 5,
    ) -> PublicIncidentsResponse:
        """Return operator-authored public incidents (active + recent resolved)."""
        repo = IncidentRepository(session)
        active = await repo.list_for_audience(audience=Audience.PUBLIC, include_resolved=False)
        all_recent = await repo.list_for_audience(audience=Audience.PUBLIC, include_resolved=True)
        resolved = [inc for inc in all_recent if inc.resolved_at is not None][:include_resolved_count]
        return PublicIncidentsResponse(
            active=[public_incident_from_row(inc) for inc in active],
            recent_resolved=[public_incident_from_row(inc) for inc in resolved],
            generated_at=_now(),
        )

    @router.get("/maintenance", response_model=PublicMaintenanceResponse)
    async def get_public_maintenance(session: SessionDep) -> PublicMaintenanceResponse:
        """Return upcoming and active public maintenance windows."""
        repo = MaintenanceRepository(session)
        moment = _now()
        windows = await repo.list_visible(audience=Audience.PUBLIC, now=moment)
        return PublicMaintenanceResponse(
            windows=[public_maintenance_from_row(w, now=moment) for w in windows],
            generated_at=moment,
        )

    @router.get("/history", response_model=PublicHistoryResponse)
    async def get_public_history(
        session: SessionDep,
        component: Annotated[str, Query(min_length=1, max_length=64)],
        days: Annotated[int, Query(ge=1, le=400)] = 90,
    ) -> PublicHistoryResponse:
        """Return one bucket per day for the public history bars on the status page."""
        components_repo = ComponentRepository(session)
        existing = await components_repo.get(component)
        if existing is None or existing.audience != Audience.PUBLIC:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unknown public component.",
            )
        history_repo = HistoryRepository(session)
        moment = _now()
        buckets = await history_repo.daily_buckets(component, days=days, now=moment)
        uptime = await history_repo.uptime_percent(component, days=days, now=moment)
        return history_response(
            component_id=component,
            days=days,
            buckets=buckets,
            uptime_percent=uptime,
        )

    @router.get("/stats", response_model=PublicStats)
    async def get_public_stats() -> PublicStats:
        """Return the headline throughput / capacity numbers for the stats strip.

        Values come from a fixed allow-list of PromQL run against the public
        Mimir tenant and are cached briefly. Missing series surface as ``null``;
        the endpoint never 5xxs on a single absent or unavailable metric.
        """
        return await stats_service.get_stats()

    return router
