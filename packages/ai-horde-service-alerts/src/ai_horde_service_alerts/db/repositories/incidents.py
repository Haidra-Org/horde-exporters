"""CRUD for operator-authored incidents and their timeline updates."""

from __future__ import annotations

import re
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai_horde_service_alerts.db.models import (
    Component,
    Incident,
    IncidentComponent,
    IncidentUpdate,
)
from ai_horde_service_alerts.db.types import Audience, IncidentSeverity, IncidentStatus

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    base = _SLUG_RE.sub("-", title.lower()).strip("-")
    return base[:60] or "incident"


class IncidentRepository:
    """Reads and writes operator-authored incidents."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to an open async session."""
        self._session = session

    async def list_for_audience(
        self,
        *,
        audience: Audience,
        include_resolved: bool = False,
        limit: int = 50,
    ) -> Sequence[Incident]:
        """Return incidents visible to ``audience``, newest first."""
        stmt = (
            select(Incident)
            .where(Incident.audience == audience)
            .options(selectinload(Incident.updates), selectinload(Incident.components))
            .order_by(Incident.started_at.desc())
            .limit(limit)
        )
        if not include_resolved:
            stmt = stmt.where(Incident.status != IncidentStatus.RESOLVED)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_all(
        self,
        *,
        include_resolved: bool = True,
        limit: int = 200,
    ) -> Sequence[Incident]:
        """Return every incident regardless of audience (admin view)."""
        stmt = (
            select(Incident)
            .options(selectinload(Incident.updates), selectinload(Incident.components))
            .order_by(Incident.started_at.desc())
            .limit(limit)
        )
        if not include_resolved:
            stmt = stmt.where(Incident.status != IncidentStatus.RESOLVED)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get(self, incident_id: UUID) -> Incident | None:
        """Look up an incident by id (with eagerly loaded relations)."""
        stmt = (
            select(Incident)
            .where(Incident.id == incident_id)
            .options(selectinload(Incident.updates), selectinload(Incident.components))
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_by_linked_alert(self, fingerprint: str) -> Incident | None:
        """Return the incident linked to ``fingerprint``, if one exists."""
        stmt = (
            select(Incident)
            .where(Incident.linked_alert_fingerprint == fingerprint)
            .where(Incident.status != IncidentStatus.RESOLVED)
            .options(selectinload(Incident.updates), selectinload(Incident.components))
            .order_by(Incident.started_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def create(
        self,
        *,
        title: str,
        audience: Audience,
        severity: IncidentSeverity,
        status: IncidentStatus,
        affected_component_ids: Sequence[str],
        body: str,
        created_by: str,
        started_at: datetime | None = None,
        linked_alert_fingerprint: str | None = None,
        now: datetime | None = None,
    ) -> Incident:
        """Persist an incident with its initial timeline entry.

        The incident is unique per slug; a 6-char random suffix is appended to
        avoid collisions. The first ``IncidentUpdate`` carries ``body`` and
        ``status_at_post = status``.
        """
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        slug = f"{_slugify(title)}-{secrets.token_hex(3)}"
        incident = Incident(
            slug=slug,
            title=title.strip(),
            audience=audience,
            severity=severity,
            status=status,
            started_at=(started_at or moment).astimezone(UTC),
            created_by=created_by,
            linked_alert_fingerprint=linked_alert_fingerprint,
        )
        self._session.add(incident)
        await self._session.flush()
        for component_id in dict.fromkeys(affected_component_ids):
            self._session.add(
                IncidentComponent(incident_id=incident.id, component_id=component_id),
            )
        if body:
            self._session.add(
                IncidentUpdate(
                    incident_id=incident.id,
                    posted_at=moment,
                    status_at_post=status,
                    body=body,
                    posted_by=created_by,
                ),
            )
        await self._session.flush()
        await self._session.refresh(incident, attribute_names=("updates", "components"))
        return incident

    async def update_metadata(
        self,
        incident: Incident,
        *,
        title: str | None = None,
        severity: IncidentSeverity | None = None,
        affected_component_ids: Sequence[str] | None = None,
    ) -> Incident:
        """Edit non-state fields. Resolved incidents are immutable."""
        if incident.status == IncidentStatus.RESOLVED:
            raise ValueError("cannot edit a resolved incident")
        if title is not None:
            incident.title = title.strip()
        if severity is not None:
            incident.severity = severity
        if affected_component_ids is not None:
            await self._replace_components(incident, affected_component_ids)
        await self._session.flush()
        return incident

    async def _replace_components(self, incident: Incident, component_ids: Sequence[str]) -> None:
        for existing in list(incident.components):
            existing_link = await self._session.get(
                IncidentComponent,
                {"incident_id": incident.id, "component_id": existing.id},
            )
            if existing_link is not None:
                await self._session.delete(existing_link)
        component_ids = list(dict.fromkeys(component_ids))
        for component_id in component_ids:
            component = await self._session.get(Component, component_id)
            if component is None:
                raise ValueError(f"unknown component: {component_id}")
            self._session.add(
                IncidentComponent(incident_id=incident.id, component_id=component_id),
            )

    async def post_update(
        self,
        incident: Incident,
        *,
        body: str,
        new_status: IncidentStatus,
        posted_by: str,
        now: datetime | None = None,
    ) -> IncidentUpdate:
        """Append a timeline entry and advance the incident's status."""
        if incident.status == IncidentStatus.RESOLVED:
            raise ValueError("cannot post updates on a resolved incident")
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        incident.status = new_status
        if new_status == IncidentStatus.RESOLVED:
            incident.resolved_at = moment
        update = IncidentUpdate(
            incident_id=incident.id,
            posted_at=moment,
            status_at_post=new_status,
            body=body,
            posted_by=posted_by,
        )
        self._session.add(update)
        await self._session.flush()
        await self._session.refresh(incident, attribute_names=("updates",))
        return update
