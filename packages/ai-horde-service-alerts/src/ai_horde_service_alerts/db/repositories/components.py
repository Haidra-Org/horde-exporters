"""Read/write helpers for the component registry."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_horde_service_alerts.db.models import Component
from ai_horde_service_alerts.db.types import Audience


class ComponentUpsertRow(TypedDict):
    """Normalized component payload accepted by :meth:`ComponentRepository.upsert_many`."""

    id: str
    name: str
    description: str
    audience: Audience
    display_order: int
    enabled: bool


class ComponentRepository:
    """Encapsulates queries against the ``components`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to an open async session."""
        self._session = session

    async def list_all(self, *, audience: Audience | None = None) -> Sequence[Component]:
        """Return components ordered by display_order, optionally filtered by audience."""
        stmt = (
            select(Component)
            .where(Component.enabled.is_(True))
            .order_by(
                Component.display_order,
                Component.id,
            )
        )
        if audience is not None:
            stmt = stmt.where(Component.audience == audience)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get(self, component_id: str) -> Component | None:
        """Return a single component by id, or None when missing."""
        return await self._session.get(Component, component_id)

    async def upsert_many(self, rows: Sequence[ComponentUpsertRow]) -> None:
        """Idempotently insert or update component rows from a registry seed.

        Each row should carry ``id``, ``name``, ``description``, ``audience``,
        ``display_order``, and optionally ``enabled``. Uses Postgres
        ``INSERT ... ON CONFLICT (id) DO UPDATE`` so deploys can rename or
        reorder safely. SQLite (used in tests) falls back to a per-row merge.
        """
        if not rows:
            return
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            stmt = pg_insert(Component).values(list(rows))
            update_cols = {
                "name": stmt.excluded.name,
                "description": stmt.excluded.description,
                "audience": stmt.excluded.audience,
                "display_order": stmt.excluded.display_order,
                "enabled": stmt.excluded.enabled,
            }
            stmt = stmt.on_conflict_do_update(index_elements=[Component.id], set_=update_cols)
            await self._session.execute(stmt)
            return
        for row in rows:
            existing = await self._session.get(Component, row["id"])
            if existing is None:
                self._session.add(Component(**row))
                continue
            existing.name = row["name"]
            existing.description = row["description"]
            existing.audience = row["audience"]
            existing.display_order = row["display_order"]
            existing.enabled = row["enabled"]
