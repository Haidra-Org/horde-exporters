"""Pydantic response models exposed to unauthenticated public consumers.

The redesigned model:

- The public surface is **structural-only**. No alert ``summary`` annotation
  text ever reaches public consumers — incident prose is operator-authored
  and lives in :class:`PublicIncidentUpdate`.
- Component status is driven by curated alerts and the external prober via
  :mod:`ai_horde_service_alerts.services.status_evaluator`; the public
  endpoint just reads the ``component_status_history`` view.
- Maintenance windows and the 90-day history bars are first-class endpoints.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_horde_service_alerts.db.types import (
    Audience,
    ComponentStatusValue,
    IncidentSeverity,
    IncidentStatus,
)


class PublicComponent(BaseModel):
    """A single component as displayed on the public status page."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    status: ComponentStatusValue
    uptime_90d: float | None = Field(
        default=None,
        description="Percentage operational time over the trailing 90d, excluding maintenance.",
    )
    last_change_at: datetime | None = None


class PublicComponentsResponse(BaseModel):
    """Wrapper for the components list (versioned for future expansion)."""

    model_config = ConfigDict(extra="forbid")

    components: list[PublicComponent] = Field(default_factory=list)
    overall: ComponentStatusValue
    generated_at: datetime


class PublicIncidentUpdate(BaseModel):
    """One operator-authored timeline entry on a public incident."""

    model_config = ConfigDict(extra="forbid")

    posted_at: datetime
    status: IncidentStatus
    body: str


class PublicIncident(BaseModel):
    """An operator-authored public incident card."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    slug: str
    title: str
    severity: IncidentSeverity
    status: IncidentStatus
    started_at: datetime
    resolved_at: datetime | None = None
    affects: list[str] = Field(
        default_factory=list,
        description="Component ids this incident says it affects.",
    )
    affects_names: list[str] = Field(
        default_factory=list,
        description="Display names for ``affects``, in the same order.",
    )
    updates: list[PublicIncidentUpdate] = Field(default_factory=list)


class PublicIncidentsResponse(BaseModel):
    """Wrapper for the public incidents list."""

    model_config = ConfigDict(extra="forbid")

    active: list[PublicIncident] = Field(default_factory=list)
    recent_resolved: list[PublicIncident] = Field(default_factory=list)
    generated_at: datetime


class PublicMaintenance(BaseModel):
    """A public-facing maintenance window."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str
    body: str
    starts_at: datetime
    ends_at: datetime
    affects: list[str] = Field(default_factory=list)
    affects_names: list[str] = Field(default_factory=list)
    is_active: bool


class PublicMaintenanceResponse(BaseModel):
    """Wrapper for upcoming + active maintenance windows."""

    model_config = ConfigDict(extra="forbid")

    windows: list[PublicMaintenance] = Field(default_factory=list)
    generated_at: datetime


class PublicHistoryDay(BaseModel):
    """Daily uptime bar bucket for the public history view."""

    model_config = ConfigDict(extra="forbid")

    date: str = Field(description="UTC ISO date (YYYY-MM-DD).")
    status_level: int = Field(
        ge=0,
        le=3,
        description="0 ok | 1 minor (degraded) | 2 major (partial/down) | 3 maintenance. "
        "Unknown/no-signal time never raises the level above 0.",
    )
    operational_seconds: int
    degraded_seconds: int
    down_seconds: int
    maintenance_seconds: int
    unknown_seconds: int


class PublicHistoryResponse(BaseModel):
    """Wrapper for the per-component history bars query."""

    model_config = ConfigDict(extra="forbid")

    component_id: str
    days: int
    buckets: list[PublicHistoryDay] = Field(default_factory=list)
    uptime_percent: float | None = None


class PublicStats(BaseModel):
    """Headline throughput / capacity numbers for the public stats strip.

    Every field is nullable: a metric that is missing from the backend (no
    series, or Mimir unavailable) renders as ``—`` on the page rather than a
    misleading ``0``. The ``*_day`` / ``*_month`` totals are AI Horde's native
    named-period counters (today / this calendar month), **not** rolling 24h /
    30d windows — the UI must label them as such.
    """

    model_config = ConfigDict(extra="forbid")

    active_image_workers: int | None = None
    active_text_workers: int | None = None
    active_alchemy_workers: int | None = Field(
        default=None,
        description="Always null today: the stats exporter does not emit an alchemy worker count.",
    )
    queued_image_requests: int | None = None
    queued_text_requests: int | None = None
    queue_drain_image_seconds: float | None = None
    queue_drain_text_seconds: float | None = None
    images_generated_day: int | None = Field(
        default=None,
        description='Images generated so far in the current day (period="day"), not a rolling 24h.',
    )
    images_generated_month: int | None = Field(
        default=None,
        description='Images generated in the current calendar month (period="month"), not a rolling 30d.',
    )
    tokens_generated_day: int | None = Field(
        default=None,
        description='Text tokens generated so far in the current day (period="day"), not a rolling 24h.',
    )
    generated_at: datetime


# Re-exports kept for callers that reach for the enums via the models module.
__all__ = [
    "Audience",
    "ComponentStatusValue",
    "IncidentSeverity",
    "IncidentStatus",
    "PublicComponent",
    "PublicComponentsResponse",
    "PublicHistoryDay",
    "PublicHistoryResponse",
    "PublicIncident",
    "PublicIncidentUpdate",
    "PublicIncidentsResponse",
    "PublicMaintenance",
    "PublicMaintenanceResponse",
    "PublicStats",
]
