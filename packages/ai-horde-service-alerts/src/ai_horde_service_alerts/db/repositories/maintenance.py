"""Scheduled maintenance window CRUD and lookups."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai_horde_service_alerts.db.models import (
    Component,
    MaintenanceComponent,
    MaintenanceWindow,
)
from ai_horde_service_alerts.db.types import Audience


class MaintenanceRepository:
    """Reads and writes scheduled maintenance windows."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to an open async session."""
        self._session = session

    async def get(self, window_id: UUID) -> MaintenanceWindow | None:
        """Fetch a single window with components eagerly loaded."""
        stmt = (
            select(MaintenanceWindow)
            .where(MaintenanceWindow.id == window_id)
            .options(selectinload(MaintenanceWindow.components))
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_visible(
        self,
        *,
        audience: Audience,
        now: datetime | None = None,
        upcoming_window_days: int = 30,
    ) -> Sequence[MaintenanceWindow]:
        """Return windows visible to ``audience`` in the upcoming/active window."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        soft_cutoff = moment + timedelta(days=upcoming_window_days)
        stmt = (
            select(MaintenanceWindow)
            .where(MaintenanceWindow.audience == audience)
            .where(MaintenanceWindow.cancelled_at.is_(None))
            .where(MaintenanceWindow.ends_at >= moment)
            .where(MaintenanceWindow.starts_at <= soft_cutoff)
            .options(selectinload(MaintenanceWindow.components))
            .order_by(MaintenanceWindow.starts_at)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_all(self, *, limit: int = 200) -> Sequence[MaintenanceWindow]:
        """Return all windows for the admin view, newest start first."""
        stmt = (
            select(MaintenanceWindow)
            .options(selectinload(MaintenanceWindow.components))
            .order_by(MaintenanceWindow.starts_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_active_uncancelled(
        self,
        *,
        now: datetime | None = None,
    ) -> Sequence[MaintenanceWindow]:
        """Return windows whose effective interval encloses ``now`` and that are uncancelled."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        stmt = (
            select(MaintenanceWindow)
            .where(MaintenanceWindow.cancelled_at.is_(None))
            .where(MaintenanceWindow.starts_at <= moment)
            .where(MaintenanceWindow.ends_at >= moment)
            .options(selectinload(MaintenanceWindow.components))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_due_to_activate(
        self,
        *,
        now: datetime | None = None,
    ) -> Sequence[MaintenanceWindow]:
        """Return windows whose start has passed but ``activated_at`` is null."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        stmt = (
            select(MaintenanceWindow)
            .where(MaintenanceWindow.cancelled_at.is_(None))
            .where(MaintenanceWindow.activated_at.is_(None))
            .where(MaintenanceWindow.starts_at <= moment)
            .where(MaintenanceWindow.ends_at > moment)
            .options(selectinload(MaintenanceWindow.components))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_due_to_deactivate(
        self,
        *,
        now: datetime | None = None,
    ) -> Sequence[MaintenanceWindow]:
        """Return windows that have ended (or were cancelled) but are still flagged active."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        stmt = (
            select(MaintenanceWindow)
            .where(MaintenanceWindow.activated_at.is_not(None))
            .where(MaintenanceWindow.deactivated_at.is_(None))
            .where(
                (MaintenanceWindow.ends_at <= moment) | (MaintenanceWindow.cancelled_at.is_not(None)),
            )
            .options(selectinload(MaintenanceWindow.components))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def create(
        self,
        *,
        title: str,
        body: str,
        audience: Audience,
        starts_at: datetime,
        ends_at: datetime,
        component_ids: Sequence[str],
        created_by: str,
    ) -> MaintenanceWindow:
        """Insert a new window with its component memberships."""
        if ends_at <= starts_at:
            raise ValueError("ends_at must be strictly after starts_at")
        window = MaintenanceWindow(
            title=title.strip(),
            body=body,
            audience=audience,
            starts_at=starts_at.astimezone(UTC),
            ends_at=ends_at.astimezone(UTC),
            created_by=created_by,
        )
        self._session.add(window)
        await self._session.flush()
        for component_id in dict.fromkeys(component_ids):
            component = await self._session.get(Component, component_id)
            if component is None:
                raise ValueError(f"unknown component: {component_id}")
            self._session.add(
                MaintenanceComponent(maintenance_id=window.id, component_id=component_id),
            )
        await self._session.flush()
        await self._session.refresh(window, attribute_names=("components",))
        return window

    async def cancel(
        self,
        window: MaintenanceWindow,
        *,
        now: datetime | None = None,
    ) -> MaintenanceWindow:
        """Mark a window cancelled. Idempotent."""
        if window.cancelled_at is None:
            window.cancelled_at = (now or datetime.now(tz=UTC)).astimezone(UTC)
        await self._session.flush()
        return window

    async def mark_activated(
        self,
        window: MaintenanceWindow,
        *,
        now: datetime | None = None,
    ) -> None:
        """Stamp ``activated_at`` so the maintenance_runner does not re-fire."""
        window.activated_at = (now or datetime.now(tz=UTC)).astimezone(UTC)
        await self._session.flush()

    async def mark_deactivated(
        self,
        window: MaintenanceWindow,
        *,
        now: datetime | None = None,
    ) -> None:
        """Stamp ``deactivated_at`` once the runner has reverted affected components."""
        window.deactivated_at = (now or datetime.now(tz=UTC)).astimezone(UTC)
        await self._session.flush()
