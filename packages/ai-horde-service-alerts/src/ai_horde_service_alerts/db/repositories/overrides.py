"""Operator-set component status overrides."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_horde_service_alerts.db.models import ComponentOverride
from ai_horde_service_alerts.db.types import ComponentStatusValue


class OverrideRepository:
    """Encapsulates operator-driven sticky pins."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to an open async session."""
        self._session = session

    async def get_active(self, component_id: str, *, now: datetime | None = None) -> ComponentOverride | None:
        """Return the currently-effective override for the component, if any."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        stmt = (
            select(ComponentOverride)
            .where(ComponentOverride.component_id == component_id)
            .where(ComponentOverride.cleared_at.is_(None))
            .where(or_(ComponentOverride.expires_at.is_(None), ComponentOverride.expires_at > moment))
            .order_by(ComponentOverride.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_active(self, *, now: datetime | None = None) -> Sequence[ComponentOverride]:
        """Return all currently-effective overrides."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        stmt = (
            select(ComponentOverride)
            .where(ComponentOverride.cleared_at.is_(None))
            .where(or_(ComponentOverride.expires_at.is_(None), ComponentOverride.expires_at > moment))
            .order_by(ComponentOverride.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def set_override(
        self,
        *,
        component_id: str,
        target_status: ComponentStatusValue,
        reason: str,
        created_by: str,
        expires_at: datetime | None,
        now: datetime | None = None,
    ) -> ComponentOverride:
        """Clear any active override for the component and create a new one."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        existing = await self.get_active(component_id, now=moment)
        if existing is not None:
            existing.cleared_at = moment
            existing.cleared_by = created_by
        new_row = ComponentOverride(
            component_id=component_id,
            target_status=target_status,
            reason=reason,
            created_by=created_by,
            expires_at=expires_at,
        )
        self._session.add(new_row)
        await self._session.flush()
        return new_row

    async def clear(
        self,
        *,
        component_id: str,
        cleared_by: str,
        now: datetime | None = None,
    ) -> ComponentOverride | None:
        """Clear the active override (if any) and return the cleared row."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        existing = await self.get_active(component_id, now=moment)
        if existing is None:
            return None
        existing.cleared_at = moment
        existing.cleared_by = cleared_by
        await self._session.flush()
        return existing

    async def get(self, override_id: UUID) -> ComponentOverride | None:
        """Look up a single override by id."""
        return await self._session.get(ComponentOverride, override_id)

    async def expire_overdue(self, *, now: datetime | None = None) -> Sequence[ComponentOverride]:
        """Mark expired overrides as cleared. Returns the rows that were just expired."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        stmt = (
            select(ComponentOverride)
            .where(ComponentOverride.cleared_at.is_(None))
            .where(and_(ComponentOverride.expires_at.is_not(None), ComponentOverride.expires_at <= moment))
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        for row in rows:
            row.cleared_at = moment
            row.cleared_by = "system:expiry"
        await self._session.flush()
        return rows
