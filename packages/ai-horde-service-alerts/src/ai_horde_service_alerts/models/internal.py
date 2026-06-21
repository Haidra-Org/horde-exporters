"""Pydantic models for raw upstream / internal-facing data."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_horde_service_alerts.db.types import (
    Audience,
    ComponentStatusValue,
    IncidentSeverity,
    IncidentStatus,
    ProbeOutcome,
)
from ai_horde_service_alerts.models.public import (
    PublicIncident,
    PublicIncidentUpdate,
    PublicMaintenance,
)


class AlertmanagerAlertStatus(BaseModel):
    """Represents an Alertmanager alert status payload."""

    state: str = "active"

    model_config = ConfigDict(extra="allow")


class AlertmanagerSilenceStatus(BaseModel):
    """Represents an Alertmanager silence status payload."""

    state: str = ""

    model_config = ConfigDict(extra="allow")


class AlertmanagerMatcher(BaseModel):
    """Represents one matcher on an Alertmanager silence."""

    name: str
    value: str
    is_regex: bool = Field(default=False, alias="isRegex")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class AlertmanagerAlert(BaseModel):
    """Represents an Alertmanager v2 alert object (passthrough subset)."""

    fingerprint: str
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    status: AlertmanagerAlertStatus = Field(default_factory=AlertmanagerAlertStatus)
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    generator_url: str | None = Field(default=None, alias="generatorURL")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class AlertmanagerSilence(BaseModel):
    """Represents an Alertmanager v2 silence object (passthrough subset)."""

    id: str
    status: AlertmanagerSilenceStatus = Field(default_factory=AlertmanagerSilenceStatus)
    matchers: list[AlertmanagerMatcher] = Field(default_factory=list)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime = Field(alias="endsAt")
    created_by: str = Field(default="", alias="createdBy")
    comment: str = ""

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class AlertmanagerStatus(BaseModel):
    """Represents the Alertmanager v2 cluster/version status payload (passthrough)."""

    cluster: dict[str, Any] | None = None
    version_info: dict[str, Any] | None = Field(default=None, alias="versionInfo")
    uptime: datetime | None = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class ProbeResultDetail(BaseModel):
    """Represents the explicit optional detail fields accepted for one probe sample."""

    model_config = ConfigDict(extra="forbid")

    status_code: int | None = None
    error: str | None = None
    message: str | None = None
    parse_error: str | None = None
    reason: str | None = None
    online_workers: int | None = None
    total_workers: int | None = None
    worker_count: int | None = None
    text_worker_count: int | None = None
    keys: list[str] | None = None


class MimirInstantSample(BaseModel):
    """Represents a single instant-vector sample from a Mimir Prometheus query."""

    metric: dict[str, str] = Field(default_factory=dict)
    timestamp: float
    value: str


class MimirInstantResult(BaseModel):
    """Represents the parsed response of a Mimir instant query."""

    result_type: str
    samples: list[MimirInstantSample] = Field(default_factory=list)


class MimirRangeSeries(BaseModel):
    """One series from a Mimir/Prometheus range query."""

    metric: dict[str, str] = Field(default_factory=dict)
    values: list[tuple[float, str]] = Field(default_factory=list)


class MimirRangeResult(BaseModel):
    """Parsed response of a Mimir range query (resultType=matrix)."""

    series: list[MimirRangeSeries] = Field(default_factory=list)


class AiHordeUser(BaseModel):
    """Represents the subset of the AI Horde find_user response we depend on."""

    id: int | None = None
    username: str | None = None
    moderator: bool = False

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Internal-only request bodies for the operator API.


class IncidentCreateRequest(BaseModel):
    """Body for ``POST /internal/incidents``."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=256)
    audience: Audience = Audience.PUBLIC
    severity: IncidentSeverity
    status: IncidentStatus = IncidentStatus.INVESTIGATING
    affected_components: list[str] = Field(default_factory=list, max_length=32)
    body: str = Field(min_length=1, max_length=4096)
    started_at: datetime | None = None


class IncidentUpdateRequest(BaseModel):
    """Body for ``PATCH /internal/incidents/{id}``."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=256)
    severity: IncidentSeverity | None = None
    affected_components: list[str] | None = Field(default=None, max_length=32)


class IncidentTimelinePostRequest(BaseModel):
    """Body for ``POST /internal/incidents/{id}/updates``."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4096)
    new_status: IncidentStatus


class IncidentResolveRequest(BaseModel):
    """Body for ``POST /internal/incidents/{id}/resolve``."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4096)


class MaintenanceCreateRequest(BaseModel):
    """Body for ``POST /internal/maintenance``."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=4096)
    audience: Audience = Audience.PUBLIC
    starts_at: datetime
    ends_at: datetime
    affected_components: list[str] = Field(default_factory=list, max_length=32)


class ComponentOverrideRequest(BaseModel):
    """Body for ``POST /internal/components/{id}/override``."""

    model_config = ConfigDict(extra="forbid")

    target_status: ComponentStatusValue
    reason: str = Field(default="", max_length=2048)
    expires_at: datetime | None = None


class AlertPromotionRequest(BaseModel):
    """Body for ``POST /internal/alerts/{fingerprint}/promote``."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=256)
    severity: IncidentSeverity
    affected_components: list[str] = Field(default_factory=list, max_length=32)
    body: str = Field(min_length=1, max_length=4096)
    audience: Audience = Audience.PUBLIC


class ProbeResultSubmission(BaseModel):
    """Body for ``POST /internal/probe-results`` (called by the prober)."""

    model_config = ConfigDict(extra="forbid")

    probe_name: str = Field(min_length=1, max_length=64)
    component_id: str = Field(min_length=1, max_length=64)
    outcome: ProbeOutcome
    observed_at: datetime
    latency_ms: int | None = Field(default=None, ge=0, le=600_000)
    detail: ProbeResultDetail | None = None


# ---------------------------------------------------------------------------
# Internal admin response shapes (re-use public shapes where possible).


class AdminIncident(PublicIncident):
    """Admin-view of an incident (audience and creator surfaced)."""

    model_config = ConfigDict(extra="forbid")

    audience: Audience
    created_by: str
    linked_alert_fingerprint: str | None = None


class AdminMaintenance(PublicMaintenance):
    """Admin-view of a maintenance window (audience and lifecycle exposed)."""

    model_config = ConfigDict(extra="forbid")

    audience: Audience
    cancelled_at: datetime | None = None
    activated_at: datetime | None = None
    deactivated_at: datetime | None = None
    created_by: str


class AdminComponent(BaseModel):
    """Admin-view component row including any active override and source."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    audience: Audience
    status: ComponentStatusValue
    last_change_at: datetime | None = None
    override_status: ComponentStatusValue | None = None
    override_reason: str | None = None
    override_expires_at: datetime | None = None
    override_id: UUID | None = None


class AdminAlertSummary(BaseModel):
    """Operator-facing condensed Alertmanager alert (still shows internal labels)."""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    alertname: str
    severity: str | None = None
    component: str | None = None
    summary: str | None = None
    value: str | None = Field(
        default=None,
        description="The alert's current value annotation (e.g. 'p95 = 2.6s'), when the rule sets one.",
    )
    started_at: datetime
    state_age_seconds: int | None = Field(
        default=None,
        description="Seconds since the alert started firing, computed at response time.",
    )
    state: str
    promoted_incident_id: UUID | None = None


class AdminAlertLogEntry(BaseModel):
    """One firing/resolved alert interval reconstructed from Mimir ``ALERTS``."""

    model_config = ConfigDict(extra="forbid")

    alertname: str
    severity: str | None = None
    component: str | None = None
    state: str = Field(description="'firing' while still active, else 'resolved'.")
    started_at: datetime
    ended_at: datetime | None = Field(
        default=None,
        description="When the alert stopped firing; null while it is still firing.",
    )
    for_seconds: int = Field(
        ge=0,
        description="Duration the alert has been (or was) firing, in seconds.",
    )


__all__ = [
    "AdminAlertLogEntry",
    "AdminAlertSummary",
    "AdminComponent",
    "AdminIncident",
    "AdminMaintenance",
    "AiHordeUser",
    "AlertPromotionRequest",
    "AlertmanagerAlert",
    "AlertmanagerAlertStatus",
    "AlertmanagerMatcher",
    "AlertmanagerSilence",
    "AlertmanagerSilenceStatus",
    "AlertmanagerStatus",
    "ComponentOverrideRequest",
    "IncidentCreateRequest",
    "IncidentResolveRequest",
    "IncidentTimelinePostRequest",
    "IncidentUpdateRequest",
    "MaintenanceCreateRequest",
    "MimirInstantResult",
    "MimirInstantSample",
    "ProbeResultDetail",
    "ProbeResultSubmission",
    "PublicIncidentUpdate",
]
