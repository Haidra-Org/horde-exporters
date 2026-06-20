"""Activate / deactivate scheduled maintenance windows on time."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from ai_horde_service_alerts.db.repositories import MaintenanceRepository
from ai_horde_service_alerts.db.session import DatabaseBundle

logger = logging.getLogger(__name__)


class MaintenanceRunner:
    """Stamp ``activated_at`` / ``deactivated_at`` so the evaluator picks them up."""

    def __init__(self, database: DatabaseBundle) -> None:
        """Bind the runner to a database bundle."""
        self._database = database

    async def tick(self, *, now: datetime | None = None) -> None:
        """Activate any due windows and deactivate any expired/cancelled ones."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        async with self._database.session() as session:
            repo = MaintenanceRepository(session)
            for window in await repo.list_due_to_activate(now=moment):
                await repo.mark_activated(window, now=moment)
                logger.info("maintenance window %s activated", window.id)
            for window in await repo.list_due_to_deactivate(now=moment):
                await repo.mark_deactivated(window, now=moment)
                logger.info("maintenance window %s deactivated", window.id)
