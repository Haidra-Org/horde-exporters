"""Compute and persist effective component status.

Inputs (every tick):

* the latest probe outcome per component (within freshness window)
* the active alerts from Alertmanager (mapped through :class:`AlertMapping`)
* the active operator override per component, if any
* whether a maintenance window is currently engaged for the component

Decision rule:

* If an operator override is active, the override status wins outright.
* Otherwise, take the **worst** of (probe-derived, alert-derived,
  maintenance-derived). ``OPERATIONAL`` is used for components that still
  sit inside the no-signal grace window after creation; ``UNKNOWN`` is used
  once the grace window has elapsed with no signal.

Persistence: only writes a row to ``component_status_history`` when the
effective status actually changes.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.clients.errors import UpstreamUnavailable
from ai_horde_service_alerts.db.repositories import (
    ComponentRepository,
    HistoryRepository,
    MaintenanceRepository,
    OverrideRepository,
    ProbeResultRepository,
)
from ai_horde_service_alerts.db.session import DatabaseBundle
from ai_horde_service_alerts.db.types import (
    PROBE_OUTCOME_TO_STATUS,
    AlertHistoryTrigger,
    ComponentStatusValue,
    HistorySource,
    HistoryTrigger,
    OverrideHistoryTrigger,
    ProbeHistoryTrigger,
    worst_status,
)
from ai_horde_service_alerts.models.internal import AlertmanagerAlert
from ai_horde_service_alerts.services.alert_mapping import AlertMapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EvaluatedComponent:
    """One row of the evaluator's per-tick output (used by tests + endpoints)."""

    component_id: str
    status: ComponentStatusValue
    source: HistorySource
    triggered_by: HistoryTrigger | None
    reason: str | None


class StatusEvaluator:
    """Run the per-tick component status computation and write history rows."""

    def __init__(
        self,
        database: DatabaseBundle,
        alertmanager_client: AlertmanagerClient,
        alert_mapping: AlertMapping,
        *,
        probe_freshness: timedelta = timedelta(minutes=15),
        no_signal_grace: timedelta = timedelta(minutes=15),
    ) -> None:
        """Bind the evaluator to its dependencies and freshness threshold."""
        self._database = database
        self._alertmanager = alertmanager_client
        self._alert_mapping = alert_mapping
        self._probe_freshness = probe_freshness
        self._no_signal_grace = no_signal_grace

    async def evaluate_once(self, *, now: datetime | None = None) -> list[EvaluatedComponent]:
        """Run one evaluation tick and persist any transitions. Returns the per-component result."""
        moment = (now or datetime.now(tz=UTC)).astimezone(UTC)

        active_alerts: list[AlertmanagerAlert] = []

        try:
            active_alerts = await self._alertmanager.list_alerts(active=True, silenced=False, inhibited=False)
        except UpstreamUnavailable as exc:
            logger.warning("alertmanager unavailable during evaluation: %s", exc)

        async with self._database.session() as session:
            components_repo = ComponentRepository(session)
            history_repo = HistoryRepository(session)
            override_repo = OverrideRepository(session)
            maintenance_repo = MaintenanceRepository(session)
            probe_repo = ProbeResultRepository(session)

            await override_repo.expire_overdue(now=moment)

            components = list(await components_repo.list_all())
            latest_probes = await probe_repo.latest_per_component(
                freshness=self._probe_freshness,
                now=moment,
            )
            alert_classes = _classify_alerts(self._alert_mapping, active_alerts)
            active_maintenance_ids = {
                component.id
                for window in await maintenance_repo.list_active_uncancelled(now=moment)
                for component in window.components
                if window.audience == component.audience or component.audience.value == "public"
            }

            results: list[EvaluatedComponent] = []
            for component in components:
                override = await override_repo.get_active(component.id, now=moment)
                if override is not None:
                    override_trigger: OverrideHistoryTrigger = {
                        "override_id": str(override.id),
                        "by": override.created_by,
                    }
                    results.append(
                        EvaluatedComponent(
                            component_id=component.id,
                            status=override.target_status,
                            source=HistorySource.OVERRIDE,
                            triggered_by=override_trigger,
                            reason=override.reason or None,
                        ),
                    )
                    continue

                probe_status: ComponentStatusValue | None = None
                probe_detail: ProbeHistoryTrigger | None = None
                probe_row = latest_probes.get(component.id)
                if probe_row is not None:
                    probe_status = PROBE_OUTCOME_TO_STATUS[probe_row.outcome]
                    probe_detail = {
                        "probe_name": probe_row.probe_name,
                        "observed_at": probe_row.observed_at.isoformat(),
                        "latency_ms": probe_row.latency_ms,
                    }

                alert_status_value: ComponentStatusValue | None
                alert_names: list[str] = []

                alert_status_value, alert_names = alert_classes.get(component.id, (None, alert_names))
                alert_detail: AlertHistoryTrigger | None = {"alertnames": list(alert_names)} if alert_names else None

                in_maintenance = component.id in active_maintenance_ids
                created_at = component.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                else:
                    created_at = created_at.astimezone(UTC)
                within_no_signal_grace = moment <= created_at + self._no_signal_grace

                effective, source, triggered_by, reason = _combine(
                    probe_status=probe_status,
                    probe_detail=probe_detail,
                    alert_status=alert_status_value,
                    alert_detail=alert_detail,
                    in_maintenance=in_maintenance,
                    within_no_signal_grace=within_no_signal_grace,
                )
                results.append(
                    EvaluatedComponent(
                        component_id=component.id,
                        status=effective,
                        source=source,
                        triggered_by=triggered_by,
                        reason=reason,
                    ),
                )

            for result in results:
                await history_repo.transition(
                    component_id=result.component_id,
                    new_status=result.status,
                    source=result.source,
                    when=moment,
                    reason=result.reason,
                    triggered_by=result.triggered_by,
                )

        return results


def _classify_alerts(
    mapping: AlertMapping,
    alerts: Iterable[AlertmanagerAlert],
) -> dict[str, tuple[ComponentStatusValue, list[str]]]:
    """Group alerts by component, returning (worst-status, alertnames)."""
    out: dict[str, tuple[ComponentStatusValue, list[str]]] = {}
    for alert in alerts:
        for hit in mapping.resolve(alert):
            existing = out.get(hit.component_id)
            if existing is None:
                out[hit.component_id] = (hit.status, [hit.alertname])
                continue
            existing_status, existing_names = existing
            new_status = worst_status(existing_status, hit.status)
            new_names = existing_names if hit.alertname in existing_names else [*existing_names, hit.alertname]
            out[hit.component_id] = (new_status, new_names)
    return out


def _combine(
    *,
    probe_status: ComponentStatusValue | None,
    probe_detail: ProbeHistoryTrigger | None,
    alert_status: ComponentStatusValue | None,
    alert_detail: AlertHistoryTrigger | None,
    in_maintenance: bool,
    within_no_signal_grace: bool,
) -> tuple[ComponentStatusValue, HistorySource, HistoryTrigger | None, str | None]:
    """Combine per-source statuses into a single effective status + provenance."""
    candidates: list[tuple[ComponentStatusValue, HistorySource, HistoryTrigger | None]] = []

    if probe_status is not None:
        candidates.append((probe_status, HistorySource.PROBER, probe_detail))
    if alert_status is not None:
        candidates.append((alert_status, HistorySource.ALERTS, alert_detail))
    if in_maintenance:
        candidates.append((ComponentStatusValue.MAINTENANCE, HistorySource.MAINTENANCE, None))

    if not candidates:
        if within_no_signal_grace:
            return (
                ComponentStatusValue.OPERATIONAL,
                HistorySource.PROBER,
                None,
                "no probe or alert signal yet (within grace window)",
            )
        return (
            ComponentStatusValue.UNKNOWN,
            HistorySource.PROBER,
            None,
            "no probe or alert signal beyond grace window",
        )

    candidates.sort(key=lambda triple: _STATUS_ORDER[triple[0]])
    worst = candidates[-1]
    return worst[0], worst[1], worst[2], None


_STATUS_ORDER: dict[ComponentStatusValue, int] = {
    ComponentStatusValue.OPERATIONAL: 0,
    ComponentStatusValue.UNKNOWN: 1,
    ComponentStatusValue.MAINTENANCE: 2,
    ComponentStatusValue.DEGRADED: 3,
    ComponentStatusValue.PARTIAL: 4,
    ComponentStatusValue.DOWN: 5,
}
