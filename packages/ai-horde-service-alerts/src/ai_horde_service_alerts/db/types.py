"""Shared enums and type helpers for the status DB."""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict


class ProbeHistoryTrigger(TypedDict):
    """Represents probe metadata persisted with a derived history transition."""

    probe_name: str
    observed_at: str
    latency_ms: int | None


class AlertHistoryTrigger(TypedDict):
    """Represents alert names that contributed to a derived history transition."""

    alertnames: list[str]


class OverrideHistoryTrigger(TypedDict):
    """Represents operator override metadata persisted with a history transition."""

    override_id: str
    by: str


class BackfillHistoryTrigger(TypedDict):
    """Represents backfill provenance persisted with a reconstructed history row."""

    alertname: str


HistoryTrigger = ProbeHistoryTrigger | AlertHistoryTrigger | OverrideHistoryTrigger | BackfillHistoryTrigger


class Audience(StrEnum):
    """Visibility audience for components, incidents, and maintenance windows."""

    PUBLIC = "public"
    INTERNAL = "internal"


class ComponentStatusValue(StrEnum):
    """Effective state a component can occupy."""

    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    PARTIAL = "partial"
    DOWN = "down"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


# Worst-of-both ordering. Maintenance is between operational and degraded so
# that it doesn't mask real failures (a real outage during a maintenance
# window still surfaces to users).
STATUS_RANK: dict[ComponentStatusValue, int] = {
    ComponentStatusValue.OPERATIONAL: 0,
    ComponentStatusValue.UNKNOWN: 1,
    ComponentStatusValue.MAINTENANCE: 2,
    ComponentStatusValue.DEGRADED: 3,
    ComponentStatusValue.PARTIAL: 4,
    ComponentStatusValue.DOWN: 5,
}


def worst_status(*values: ComponentStatusValue) -> ComponentStatusValue:
    """Return the worst (highest-rank) status across the inputs.

    Falls back to ``UNKNOWN`` when called with no arguments.
    """
    if not values:
        return ComponentStatusValue.UNKNOWN
    worst = values[0]
    for value in values[1:]:
        if STATUS_RANK[value] > STATUS_RANK[worst]:
            worst = value
    return worst


class HistorySource(StrEnum):
    """Origin of a row in component_status_history."""

    PROBER = "prober"
    ALERTS = "alerts"
    OVERRIDE = "override"
    MAINTENANCE = "maintenance"
    INITIAL = "initial"
    BACKFILL = "backfill"


class IncidentSeverity(StrEnum):
    """Severity buckets for operator-authored incidents."""

    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    """Workflow state of an operator-authored incident."""

    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class ProbeOutcome(StrEnum):
    """Outcome of a single blackbox probe sample."""

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


PROBE_OUTCOME_TO_STATUS: dict[ProbeOutcome, ComponentStatusValue] = {
    ProbeOutcome.OK: ComponentStatusValue.OPERATIONAL,
    ProbeOutcome.DEGRADED: ComponentStatusValue.DEGRADED,
    ProbeOutcome.DOWN: ComponentStatusValue.DOWN,
}
