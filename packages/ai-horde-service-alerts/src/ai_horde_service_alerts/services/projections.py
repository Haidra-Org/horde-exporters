"""Pure conversion helpers from ORM rows to public/admin Pydantic shapes."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from ai_horde_service_alerts.db.models import (
    Component,
    Incident,
    MaintenanceWindow,
)
from ai_horde_service_alerts.db.repositories.history import DailyBucket
from ai_horde_service_alerts.db.types import ComponentStatusValue
from ai_horde_service_alerts.models.internal import (
    AdminComponent,
    AdminIncident,
    AdminMaintenance,
)
from ai_horde_service_alerts.models.public import (
    PublicComponent,
    PublicHistoryDay,
    PublicHistoryResponse,
    PublicIncident,
    PublicIncidentUpdate,
    PublicMaintenance,
)


def public_incident_from_row(incident: Incident) -> PublicIncident:
    """Project an :class:`Incident` to a public-safe DTO."""
    affects = [c.id for c in incident.components]
    affects_names = [c.name for c in incident.components]
    updates = [
        PublicIncidentUpdate(posted_at=u.posted_at, status=u.status_at_post, body=u.body)
        for u in sorted(incident.updates, key=lambda u: u.posted_at)
    ]
    return PublicIncident(
        id=incident.id,
        slug=incident.slug,
        title=incident.title,
        severity=incident.severity,
        status=incident.status,
        started_at=incident.started_at,
        resolved_at=incident.resolved_at,
        affects=affects,
        affects_names=affects_names,
        updates=updates,
    )


def admin_incident_from_row(incident: Incident) -> AdminIncident:
    """Project an :class:`Incident` to an admin DTO (all fields surfaced)."""
    base = public_incident_from_row(incident)
    return AdminIncident(
        **base.model_dump(),
        audience=incident.audience,
        created_by=incident.created_by,
        linked_alert_fingerprint=incident.linked_alert_fingerprint,
    )


def public_maintenance_from_row(window: MaintenanceWindow, *, now: datetime | None = None) -> PublicMaintenance:
    """Project a :class:`MaintenanceWindow` to a public DTO."""
    moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
    return PublicMaintenance(
        id=window.id,
        title=window.title,
        body=window.body,
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        affects=[c.id for c in window.components],
        affects_names=[c.name for c in window.components],
        is_active=(window.cancelled_at is None and window.starts_at <= moment <= window.ends_at),
    )


def admin_maintenance_from_row(window: MaintenanceWindow, *, now: datetime | None = None) -> AdminMaintenance:
    """Project a :class:`MaintenanceWindow` to an admin DTO."""
    base = public_maintenance_from_row(window, now=now)
    return AdminMaintenance(
        **base.model_dump(),
        audience=window.audience,
        cancelled_at=window.cancelled_at,
        activated_at=window.activated_at,
        deactivated_at=window.deactivated_at,
        created_by=window.created_by,
    )


def public_component(
    *,
    component: Component,
    status: ComponentStatusValue,
    last_change_at: datetime | None,
    uptime_90d: float | None,
) -> PublicComponent:
    """Construct a :class:`PublicComponent` row."""
    return PublicComponent(
        id=component.id,
        name=component.name,
        description=component.description,
        status=status,
        uptime_90d=uptime_90d,
        last_change_at=last_change_at,
    )


def admin_component(
    *,
    component: Component,
    status: ComponentStatusValue,
    last_change_at: datetime | None,
    override_status: ComponentStatusValue | None,
    override_reason: str | None,
    override_expires_at: datetime | None,
    override_id: UUID | None,
) -> AdminComponent:
    """Construct an :class:`AdminComponent` row."""
    return AdminComponent(
        id=component.id,
        name=component.name,
        description=component.description,
        audience=component.audience,
        status=status,
        last_change_at=last_change_at,
        override_status=override_status,
        override_reason=override_reason,
        override_expires_at=override_expires_at,
        override_id=override_id,
    )


def history_response(
    *,
    component_id: str,
    days: int,
    buckets: Iterable[DailyBucket],
    uptime_percent: float | None,
) -> PublicHistoryResponse:
    """Wrap daily buckets into :class:`PublicHistoryResponse`."""
    return PublicHistoryResponse(
        component_id=component_id,
        days=days,
        uptime_percent=uptime_percent,
        buckets=[
            PublicHistoryDay(
                date=b.date,
                status_level=b.status_level,
                operational_seconds=b.operational_seconds,
                degraded_seconds=b.degraded_seconds,
                down_seconds=b.down_seconds,
                maintenance_seconds=b.maintenance_seconds,
                unknown_seconds=b.unknown_seconds,
            )
            for b in buckets
        ],
    )
