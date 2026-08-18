"""Activate / deactivate scheduled maintenance windows on time, and prune old probe samples."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from ai_horde_service_alerts.db.repositories import MaintenanceRepository, ProbeResultRepository
from ai_horde_service_alerts.db.session import DatabaseBundle

logger = logging.getLogger(__name__)


class MaintenanceRunner:
    """Stamp ``activated_at`` / ``deactivated_at`` so the evaluator picks them up.

    Also owns housekeeping that has to happen on a clock rather than on a
    request: pruning ``probe_results`` older than ``probe_result_retention``.
    The evaluator only ever needs the freshest sample per component, and the
    admin ``recent`` view is limit-bounded, so unbounded growth of that table
    is pure cost (before this existed it reached ~450k rows in two months and
    the evaluator's per-tick read of it pegged a core).
    """

    def __init__(
        self,
        database: DatabaseBundle,
        *,
        probe_result_retention: timedelta | None = timedelta(days=7),
    ) -> None:
        """Bind the runner to a database bundle.

        ``probe_result_retention`` of ``None`` disables pruning.
        """
        self._database = database
        self._probe_result_retention = probe_result_retention

    async def tick(self, *, now: datetime | None = None) -> None:
        """Activate/deactivate due windows, then prune expired probe samples."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)
        async with self._database.session() as session:
            repo = MaintenanceRepository(session)
            for window in await repo.list_due_to_activate(now=moment):
                await repo.mark_activated(window, now=moment)
                logger.info("maintenance window %s activated", window.id)
            for window in await repo.list_due_to_deactivate(now=moment):
                await repo.mark_deactivated(window, now=moment)
                logger.info("maintenance window %s deactivated", window.id)

        if self._probe_result_retention is not None:
            async with self._database.session() as session:
                deleted = await ProbeResultRepository(session).trim_older_than(
                    cutoff=moment - self._probe_result_retention,
                )
            if deleted:
                logger.info("pruned %d probe_results older than %s", deleted, self._probe_result_retention)
