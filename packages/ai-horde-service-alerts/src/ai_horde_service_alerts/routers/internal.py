"""Internal routes for raw upstream data and operator writes.

Most routes are moderator-only. Probe ingestion is exposed through a
separate router that requires only the prober shared secret.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_horde_service_alerts.auth import ModeratorIdentity
from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.clients.errors import UpstreamUnavailable
from ai_horde_service_alerts.clients.mimir import MimirClient
from ai_horde_service_alerts.db.repositories import (
    ComponentRepository,
    HistoryRepository,
    IncidentRepository,
    MaintenanceRepository,
    OverrideRepository,
    ProbeResultRepository,
)
from ai_horde_service_alerts.db.types import (
    ComponentStatusValue,
    IncidentStatus,
    OverrideHistoryTrigger,
)
from ai_horde_service_alerts.deps import DependencyBundle
from ai_horde_service_alerts.models.internal import (
    AdminAlertSummary,
    AdminComponent,
    AdminIncident,
    AdminMaintenance,
    AlertmanagerAlert,
    AlertmanagerSilence,
    AlertmanagerStatus,
    AlertPromotionRequest,
    ComponentOverrideRequest,
    IncidentCreateRequest,
    IncidentResolveRequest,
    IncidentTimelinePostRequest,
    IncidentUpdateRequest,
    MaintenanceCreateRequest,
    MimirInstantResult,
    ProbeResultSubmission,
)
from ai_horde_service_alerts.services.projections import (
    admin_component,
    admin_incident_from_row,
    admin_maintenance_from_row,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def create_router(dependencies: DependencyBundle) -> APIRouter:
    """Create moderator-only internal routes from explicit dependency callables."""
    router = APIRouter(
        prefix="/api/v1/internal",
        tags=["internal"],
        dependencies=[Depends(dependencies.require_moderator)],
    )

    AlertmanagerDep = Annotated[AlertmanagerClient, Depends(dependencies.get_alertmanager_client)]
    MimirDep = Annotated[MimirClient, Depends(dependencies.get_mimir_client)]
    SessionDep = Annotated[AsyncSession, Depends(dependencies.get_session)]
    ModeratorDep = Annotated[ModeratorIdentity, Depends(dependencies.require_moderator)]

    # ---- Existing read-through endpoints (kept) ----

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

    @router.get("/alerts/summary", response_model=list[AdminAlertSummary])
    async def list_internal_alerts_summary(
        alertmanager: AlertmanagerDep,
        session: SessionDep,
    ) -> list[AdminAlertSummary]:
        """Condensed alert list with linked-incident hints (for the promotion UI)."""
        try:
            alerts = await alertmanager.list_alerts(active=True, silenced=False, inhibited=False)
        except UpstreamUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alertmanager unavailable.",
            ) from exc
        repo = IncidentRepository(session)
        out: list[AdminAlertSummary] = []
        for alert in alerts:
            existing = await repo.get_by_linked_alert(alert.fingerprint)
            state_value = alert.status.state or "active"
            out.append(
                AdminAlertSummary(
                    fingerprint=alert.fingerprint,
                    alertname=alert.labels.get("alertname", "unknown"),
                    severity=alert.labels.get("severity"),
                    component=alert.labels.get("component"),
                    summary=alert.annotations.get("summary"),
                    started_at=alert.starts_at,
                    state=state_value,
                    promoted_incident_id=existing.id if existing is not None else None,
                ),
            )
        return out

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

    # ---- Incidents ----

    @router.get("/incidents", response_model=list[AdminIncident])
    async def list_admin_incidents(
        session: SessionDep,
        include_resolved: bool = True,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[AdminIncident]:
        """List every incident regardless of audience for the operator UI."""
        repo = IncidentRepository(session)
        incidents = await repo.list_all(include_resolved=include_resolved, limit=limit)
        return [admin_incident_from_row(i) for i in incidents]

    @router.get("/incidents/{incident_id}", response_model=AdminIncident)
    async def get_admin_incident(session: SessionDep, incident_id: UUID) -> AdminIncident:
        """Fetch one incident by id."""
        incident = await IncidentRepository(session).get(incident_id)
        if incident is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found.")
        return admin_incident_from_row(incident)

    @router.post(
        "/incidents",
        response_model=AdminIncident,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_admin_incident(
        session: SessionDep,
        moderator: ModeratorDep,
        body: IncidentCreateRequest,
    ) -> AdminIncident:
        """Create a new operator-authored incident with one initial timeline entry."""
        await _ensure_components(session, body.affected_components)
        repo = IncidentRepository(session)
        incident = await repo.create(
            title=body.title,
            audience=body.audience,
            severity=body.severity,
            status=body.status,
            affected_component_ids=body.affected_components,
            body=body.body,
            created_by=moderator.username or "unknown",
            started_at=body.started_at,
        )
        return admin_incident_from_row(incident)

    @router.patch("/incidents/{incident_id}", response_model=AdminIncident)
    async def patch_admin_incident(
        session: SessionDep,
        incident_id: UUID,
        body: IncidentUpdateRequest,
    ) -> AdminIncident:
        """Edit metadata on an open incident (title / severity / affected components)."""
        repo = IncidentRepository(session)
        incident = await repo.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found.")
        if body.affected_components is not None:
            await _ensure_components(session, body.affected_components)
        try:
            incident = await repo.update_metadata(
                incident,
                title=body.title,
                severity=body.severity,
                affected_component_ids=body.affected_components,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return admin_incident_from_row(incident)

    @router.post("/incidents/{incident_id}/updates", response_model=AdminIncident)
    async def post_admin_incident_update(
        session: SessionDep,
        moderator: ModeratorDep,
        incident_id: UUID,
        body: IncidentTimelinePostRequest,
    ) -> AdminIncident:
        """Append a timeline entry and advance the incident's status."""
        repo = IncidentRepository(session)
        incident = await repo.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found.")
        try:
            await repo.post_update(
                incident,
                body=body.body,
                new_status=body.new_status,
                posted_by=moderator.username or "unknown",
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return admin_incident_from_row(incident)

    @router.post("/incidents/{incident_id}/resolve", response_model=AdminIncident)
    async def resolve_admin_incident(
        session: SessionDep,
        moderator: ModeratorDep,
        incident_id: UUID,
        body: IncidentResolveRequest,
    ) -> AdminIncident:
        """Mark an incident resolved with a final timeline entry."""
        repo = IncidentRepository(session)
        incident = await repo.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found.")
        try:
            await repo.post_update(
                incident,
                body=body.body,
                new_status=IncidentStatus.RESOLVED,
                posted_by=moderator.username or "unknown",
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return admin_incident_from_row(incident)

    # ---- Alert promotion ----

    @router.post(
        "/alerts/{fingerprint}/promote",
        response_model=AdminIncident,
        status_code=status.HTTP_201_CREATED,
    )
    async def promote_alert(
        session: SessionDep,
        moderator: ModeratorDep,
        alertmanager: AlertmanagerDep,
        fingerprint: str,
        body: AlertPromotionRequest,
    ) -> AdminIncident:
        """Create an operator-authored incident linked to an Alertmanager fingerprint.

        The original alert summary is intentionally not copied; the operator
        supplies title and body.
        """
        try:
            alerts = await alertmanager.list_alerts(active=True, silenced=False, inhibited=False)
        except UpstreamUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alertmanager unavailable.",
            ) from exc
        match = next((a for a in alerts if a.fingerprint == fingerprint), None)
        if match is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active alert with that fingerprint.",
            )
        await _ensure_components(session, body.affected_components)
        repo = IncidentRepository(session)
        existing = await repo.get_by_linked_alert(fingerprint)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Alert already promoted to incident {existing.id}.",
            )
        incident = await repo.create(
            title=body.title,
            audience=body.audience,
            severity=body.severity,
            status=IncidentStatus.INVESTIGATING,
            affected_component_ids=body.affected_components,
            body=body.body,
            created_by=moderator.username or "unknown",
            linked_alert_fingerprint=fingerprint,
        )
        return admin_incident_from_row(incident)

    # ---- Maintenance ----

    @router.get("/maintenance", response_model=list[AdminMaintenance])
    async def list_admin_maintenance(session: SessionDep) -> list[AdminMaintenance]:
        """List every maintenance window for the operator UI."""
        repo = MaintenanceRepository(session)
        windows = await repo.list_all()
        moment = _now()
        return [admin_maintenance_from_row(w, now=moment) for w in windows]

    @router.post(
        "/maintenance",
        response_model=AdminMaintenance,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_admin_maintenance(
        session: SessionDep,
        moderator: ModeratorDep,
        body: MaintenanceCreateRequest,
    ) -> AdminMaintenance:
        """Schedule a new maintenance window."""
        await _ensure_components(session, body.affected_components)
        repo = MaintenanceRepository(session)
        try:
            window = await repo.create(
                title=body.title,
                body=body.body,
                audience=body.audience,
                starts_at=body.starts_at,
                ends_at=body.ends_at,
                component_ids=body.affected_components,
                created_by=moderator.username or "unknown",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return admin_maintenance_from_row(window)

    @router.post("/maintenance/{window_id}/cancel", response_model=AdminMaintenance)
    async def cancel_admin_maintenance(
        session: SessionDep,
        window_id: UUID,
    ) -> AdminMaintenance:
        """Cancel a maintenance window. Idempotent."""
        repo = MaintenanceRepository(session)
        window = await repo.get(window_id)
        if window is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window not found.")
        await repo.cancel(window)
        return admin_maintenance_from_row(window)

    # ---- Component overrides ----

    @router.get("/components", response_model=list[AdminComponent])
    async def list_admin_components(session: SessionDep) -> list[AdminComponent]:
        """List every component (public + internal) with derived + override status."""
        components_repo = ComponentRepository(session)
        history_repo = HistoryRepository(session)
        override_repo = OverrideRepository(session)
        moment = _now()
        rows = []
        for component in await components_repo.list_all():
            open_slice = await history_repo.get_open(component.id)
            override = await override_repo.get_active(component.id, now=moment)
            effective_status = (
                override.target_status
                if override is not None
                else (open_slice.status if open_slice else ComponentStatusValue.UNKNOWN)
            )
            rows.append(
                admin_component(
                    component=component,
                    status=effective_status,
                    last_change_at=open_slice.started_at if open_slice else None,
                    override_status=override.target_status if override else None,
                    override_reason=override.reason if override else None,
                    override_expires_at=override.expires_at if override else None,
                    override_id=override.id if override else None,
                ),
            )
        return rows

    @router.post(
        "/components/{component_id}/override",
        response_model=AdminComponent,
    )
    async def set_component_override(
        session: SessionDep,
        moderator: ModeratorDep,
        component_id: str,
        body: ComponentOverrideRequest,
    ) -> AdminComponent:
        """Pin a component to ``body.target_status`` until the operator clears it."""
        components_repo = ComponentRepository(session)
        component = await components_repo.get(component_id)
        if component is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Component not found.")
        override_repo = OverrideRepository(session)
        history_repo = HistoryRepository(session)
        moment = _now()
        override = await override_repo.set_override(
            component_id=component_id,
            target_status=body.target_status,
            reason=body.reason,
            created_by=moderator.username or "unknown",
            expires_at=body.expires_at,
            now=moment,
        )
        override_trigger: OverrideHistoryTrigger = {
            "override_id": str(override.id),
            "by": moderator.username or "unknown",
        }
        await history_repo.transition(
            component_id=component_id,
            new_status=body.target_status,
            source=__import__(
                "ai_horde_service_alerts.db.types",
                fromlist=["HistorySource"],
            ).HistorySource.OVERRIDE,
            when=moment,
            reason=body.reason or None,
            triggered_by=override_trigger,
        )
        open_slice = await history_repo.get_open(component_id)
        return admin_component(
            component=component,
            status=body.target_status,
            last_change_at=open_slice.started_at if open_slice else moment,
            override_status=override.target_status,
            override_reason=override.reason or None,
            override_expires_at=override.expires_at,
            override_id=override.id,
        )

    @router.post("/components/{component_id}/override/clear", response_model=AdminComponent)
    async def clear_component_override(
        session: SessionDep,
        moderator: ModeratorDep,
        component_id: str,
    ) -> AdminComponent:
        """Clear any active override; the evaluator resumes authority on the next tick."""
        components_repo = ComponentRepository(session)
        component = await components_repo.get(component_id)
        if component is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Component not found.")
        override_repo = OverrideRepository(session)
        await override_repo.clear(
            component_id=component_id,
            cleared_by=moderator.username or "unknown",
        )
        history_repo = HistoryRepository(session)
        open_slice = await history_repo.get_open(component_id)
        return admin_component(
            component=component,
            status=open_slice.status if open_slice else ComponentStatusValue.UNKNOWN,
            last_change_at=open_slice.started_at if open_slice else None,
            override_status=None,
            override_reason=None,
            override_expires_at=None,
            override_id=None,
        )

    return router


def create_probe_router(dependencies: DependencyBundle) -> APIRouter:
    """Create the probe-ingestion router guarded only by the prober secret."""
    router = APIRouter(prefix="/api/v1/internal", tags=["internal"])

    SessionDep = Annotated[AsyncSession, Depends(dependencies.get_session)]

    @router.post(
        "/probe-results",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(dependencies.require_prober_secret)],
    )
    async def ingest_probe_result(
        session: SessionDep,
        body: ProbeResultSubmission,
    ) -> dict[str, str]:
        """Accept one probe sample from the external prober."""
        components_repo = ComponentRepository(session)
        component = await components_repo.get(body.component_id)
        if component is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown component: {body.component_id}",
            )
        repo = ProbeResultRepository(session)
        await repo.record(
            probe_name=body.probe_name,
            component_id=body.component_id,
            outcome=body.outcome,
            observed_at=body.observed_at,
            latency_ms=body.latency_ms,
            detail=body.detail,
        )
        return {"status": "accepted"}

    return router


async def _ensure_components(session: AsyncSession, ids: list[str]) -> None:
    repo = ComponentRepository(session)
    for component_id in ids:
        if await repo.get(component_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown component: {component_id}",
            )
