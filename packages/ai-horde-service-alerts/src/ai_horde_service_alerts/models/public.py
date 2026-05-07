"""Pydantic response models exposed to unauthenticated public consumers.

These models are intentionally narrow: only fields and label keys that have
been explicitly allowlisted should ever populate them. See
:mod:`ai_horde_service_alerts.sanitize` for the projection logic.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class OverallStatus(StrEnum):
    """Coarse overall service health rollup."""

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class ComponentStatus(BaseModel):
    """Represents a single component's coarse health badge."""

    name: str
    status: OverallStatus
    since: datetime | None = None


class ServiceStatusResponse(BaseModel):
    """Represents the public service-status rollup response."""

    overall: OverallStatus
    components: list[ComponentStatus] = Field(default_factory=list)
    generated_at: datetime


class PublicIncident(BaseModel):
    """Represents a sanitized alert projection safe for public exposure."""

    name: str
    severity: str | None = None
    component: str | None = None
    service: str | None = None
    summary: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    state: str = "active"


class PublicIncidentsResponse(BaseModel):
    """Represents the public incidents-list response."""

    active: list[PublicIncident] = Field(default_factory=list)
    generated_at: datetime


class PublicSilenceSummary(BaseModel):
    """Represents an aggregate, non-attributing silence summary."""

    active_silences: int
    pending_silences: int
    components_silenced: list[str] = Field(default_factory=list)
    generated_at: datetime
