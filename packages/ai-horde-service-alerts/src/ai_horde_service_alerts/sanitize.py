"""Pure projection helpers from raw upstream payloads to public-safe DTOs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime

from ai_horde_service_alerts.models.internal import (
    AlertmanagerAlert,
    AlertmanagerSilence,
    MimirInstantResult,
)
from ai_horde_service_alerts.models.public import (
    ComponentStatus,
    OverallStatus,
    PublicIncident,
    PublicIncidentsResponse,
    PublicSilenceSummary,
    ServiceStatusResponse,
)

_SEVERITY_TO_STATUS: dict[str, OverallStatus] = {
    "critical": OverallStatus.DOWN,
    "page": OverallStatus.DOWN,
    "high": OverallStatus.DEGRADED,
    "warning": OverallStatus.DEGRADED,
    "info": OverallStatus.OK,
}


def now_utc() -> datetime:
    """Return the current UTC time. Indirection allows tests to monkeypatch."""
    return datetime.now(tz=UTC)


def redact_alert(
    alert: AlertmanagerAlert,
    *,
    label_allowlist: frozenset[str],
    annotation_allowlist: frozenset[str],
) -> PublicIncident:
    """Project a raw Alertmanager alert into a sanitized :class:`PublicIncident`.

    Only labels in ``label_allowlist`` and annotations in
    ``annotation_allowlist`` are read; everything else is dropped. The
    resulting model contains no internal labels (``instance``, ``pod``,
    ``__name__``, etc.).
    """
    labels = {k: v for k, v in alert.labels.items() if k in label_allowlist}
    annotations = {k: v for k, v in alert.annotations.items() if k in annotation_allowlist}
    state_obj = alert.status.get("state") if isinstance(alert.status, dict) else None
    return PublicIncident(
        name=labels.get("alertname", "unknown"),
        severity=labels.get("severity"),
        component=labels.get("component"),
        service=labels.get("service"),
        summary=annotations.get("summary"),
        started_at=alert.starts_at,
        ended_at=alert.ends_at,
        state=str(state_obj) if state_obj else "active",
    )


def build_public_incidents_response(
    alerts: Iterable[AlertmanagerAlert],
    *,
    label_allowlist: frozenset[str],
    annotation_allowlist: frozenset[str],
) -> PublicIncidentsResponse:
    """Build a :class:`PublicIncidentsResponse` from raw Alertmanager alerts."""
    active = [
        redact_alert(
            alert,
            label_allowlist=label_allowlist,
            annotation_allowlist=annotation_allowlist,
        )
        for alert in alerts
    ]
    return PublicIncidentsResponse(active=active, generated_at=now_utc())


def summarize_silences(silences: Iterable[AlertmanagerSilence]) -> PublicSilenceSummary:
    """Aggregate silences into a :class:`PublicSilenceSummary` without attribution."""
    active = 0
    pending = 0
    components: Counter[str] = Counter()
    for silence in silences:
        state = ""
        if isinstance(silence.status, dict):
            raw_state = silence.status.get("state")
            if isinstance(raw_state, str):
                state = raw_state
        if state == "active":
            active += 1
        elif state == "pending":
            pending += 1
        for matcher in silence.matchers:
            if not isinstance(matcher, dict):
                continue
            if matcher.get("name") == "component":
                value = matcher.get("value")
                if isinstance(value, str):
                    components[value] += 1
    return PublicSilenceSummary(
        active_silences=active,
        pending_silences=pending,
        components_silenced=sorted(components),
        generated_at=now_utc(),
    )


def compute_overall_status(
    component_results: dict[str, MimirInstantResult],
    active_alerts: Iterable[AlertmanagerAlert],
) -> ServiceStatusResponse:
    """Roll component health probes plus active alerts into a :class:`ServiceStatusResponse`.

    A component is reported ``ok`` when its instant query returned at least
    one sample whose value is non-zero, ``down`` when all samples are zero or
    missing, and ``unknown`` when the query returned no samples at all.
    Active alerts whose ``component`` label matches a component name escalate
    that component to at least ``degraded`` (or ``down`` for critical
    severities). The overall status is the worst of the per-component
    statuses.
    """
    components: list[ComponentStatus] = []
    component_statuses: dict[str, OverallStatus] = {}
    for name, result in component_results.items():
        component_statuses[name] = _result_to_status(result)

    for alert in active_alerts:
        component = alert.labels.get("component")
        if not component or component not in component_statuses:
            continue
        severity = (alert.labels.get("severity") or "").lower()
        escalation = _SEVERITY_TO_STATUS.get(severity, OverallStatus.DEGRADED)
        component_statuses[component] = _max_status(component_statuses[component], escalation)

    for name, current_status in component_statuses.items():
        components.append(ComponentStatus(name=name, status=current_status))

    overall = _aggregate_overall(component_statuses.values())
    return ServiceStatusResponse(overall=overall, components=components, generated_at=now_utc())


def _result_to_status(result: MimirInstantResult) -> OverallStatus:
    if not result.samples:
        return OverallStatus.UNKNOWN
    for sample in result.samples:
        try:
            numeric = float(sample.value)
        except ValueError:
            return OverallStatus.UNKNOWN
        if numeric == 0.0:
            return OverallStatus.DOWN
    return OverallStatus.OK


_STATUS_RANK: dict[OverallStatus, int] = {
    OverallStatus.OK: 0,
    OverallStatus.UNKNOWN: 1,
    OverallStatus.DEGRADED: 2,
    OverallStatus.DOWN: 3,
}


def _max_status(left: OverallStatus, right: OverallStatus) -> OverallStatus:
    return left if _STATUS_RANK[left] >= _STATUS_RANK[right] else right


def _aggregate_overall(statuses: Iterable[OverallStatus]) -> OverallStatus:
    aggregate = OverallStatus.OK
    saw_any = False
    for current in statuses:
        saw_any = True
        aggregate = _max_status(aggregate, current)
    if not saw_any:
        return OverallStatus.UNKNOWN
    return aggregate
