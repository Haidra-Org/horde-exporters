"""Unit tests for sanitize.py — verifies internal labels never leak."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ai_horde_service_alerts.models.internal import (
    AlertmanagerAlert,
    AlertmanagerSilence,
    MimirInstantResult,
    MimirInstantSample,
)
from ai_horde_service_alerts.models.public import OverallStatus
from ai_horde_service_alerts.sanitize import (
    build_public_incidents_response,
    compute_overall_status,
    redact_alert,
    summarize_silences,
)

LABEL_ALLOWLIST = frozenset({"severity", "component", "service", "alertname"})
ANNOTATION_ALLOWLIST = frozenset({"summary"})

FORBIDDEN_LABELS = ("instance", "pod", "__name__", "namespace", "tenant")
FORBIDDEN_ANNOTATIONS = ("description", "runbook_url", "internal_link")


def _alert(**overrides: Any) -> AlertmanagerAlert:
    base = {
        "fingerprint": "abc123",
        "startsAt": datetime(2025, 1, 1, tzinfo=UTC),
        "status": {"state": "active"},
        "labels": {
            "alertname": "TestAlert",
            "severity": "warning",
            "component": "frontpage",
            "service": "ai-horde",
            "instance": "10.0.0.1:9100",
            "pod": "secret-pod-name",
            "__name__": "up",
        },
        "annotations": {
            "summary": "public summary",
            "description": "internal description must not leak",
            "runbook_url": "https://internal.example",
        },
    }
    base.update(overrides)
    return AlertmanagerAlert.model_validate(base)


def test_redact_alert_drops_forbidden_labels() -> None:
    alert = _alert()
    incident = redact_alert(
        alert,
        label_allowlist=LABEL_ALLOWLIST,
        annotation_allowlist=ANNOTATION_ALLOWLIST,
    )
    serialized = incident.model_dump_json()
    for forbidden in (*FORBIDDEN_LABELS, *FORBIDDEN_ANNOTATIONS):
        assert forbidden not in serialized
    assert incident.name == "TestAlert"
    assert incident.severity == "warning"
    assert incident.summary == "public summary"


def test_build_public_incidents_response_never_leaks_internal_keys() -> None:
    response = build_public_incidents_response(
        [_alert(), _alert(fingerprint="def456")],
        label_allowlist=LABEL_ALLOWLIST,
        annotation_allowlist=ANNOTATION_ALLOWLIST,
    )
    payload = response.model_dump_json()
    for forbidden in (*FORBIDDEN_LABELS, *FORBIDDEN_ANNOTATIONS):
        assert forbidden not in payload


def test_summarize_silences_aggregates_state_and_components() -> None:
    silences = [
        AlertmanagerSilence.model_validate(
            {
                "id": "s1",
                "status": {"state": "active"},
                "matchers": [{"name": "component", "value": "frontpage", "isRegex": False}],
                "startsAt": "2025-01-01T00:00:00Z",
                "endsAt": "2025-01-02T00:00:00Z",
                "createdBy": "alice",
                "comment": "private comment",
            },
        ),
        AlertmanagerSilence.model_validate(
            {
                "id": "s2",
                "status": {"state": "pending"},
                "matchers": [{"name": "component", "value": "ai-horde", "isRegex": False}],
                "startsAt": "2025-01-01T00:00:00Z",
                "endsAt": "2025-01-02T00:00:00Z",
                "createdBy": "bob",
                "comment": "pending one",
            },
        ),
    ]
    summary = summarize_silences(silences)
    payload = summary.model_dump_json()
    assert summary.active_silences == 1
    assert summary.pending_silences == 1
    assert sorted(summary.components_silenced) == ["ai-horde", "frontpage"]
    assert "alice" not in payload
    assert "bob" not in payload
    assert "private comment" not in payload


def test_compute_overall_status_aggregates_components() -> None:
    samples_ok = MimirInstantResult(
        result_type="vector",
        samples=[MimirInstantSample(metric={"job": "frontpage"}, timestamp=0.0, value="1")],
    )
    samples_down = MimirInstantResult(
        result_type="vector",
        samples=[MimirInstantSample(metric={"job": "ai-horde"}, timestamp=0.0, value="0")],
    )
    response = compute_overall_status(
        {"frontpage": samples_ok, "ai-horde": samples_down},
        active_alerts=[],
    )
    assert response.overall == OverallStatus.DOWN
    name_to_status = {c.name: c.status for c in response.components}
    assert name_to_status["frontpage"] == OverallStatus.OK
    assert name_to_status["ai-horde"] == OverallStatus.DOWN


def test_compute_overall_status_escalates_via_critical_alert() -> None:
    samples_ok = MimirInstantResult(
        result_type="vector",
        samples=[MimirInstantSample(metric={}, timestamp=0.0, value="1")],
    )
    response = compute_overall_status(
        {"frontpage": samples_ok},
        active_alerts=[_alert(labels={"alertname": "X", "severity": "critical", "component": "frontpage"})],
    )
    assert response.overall == OverallStatus.DOWN


def test_compute_overall_status_unknown_when_no_components() -> None:
    response = compute_overall_status({}, active_alerts=[])
    assert response.overall == OverallStatus.UNKNOWN
