"""Pydantic models for raw upstream / internal-facing data."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AlertmanagerAlert(BaseModel):
    """Represents an Alertmanager v2 alert object (passthrough subset)."""

    fingerprint: str
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    status: dict[str, object] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    generator_url: str | None = Field(default=None, alias="generatorURL")

    model_config = {"populate_by_name": True, "extra": "allow"}


class AlertmanagerSilence(BaseModel):
    """Represents an Alertmanager v2 silence object (passthrough subset)."""

    id: str
    status: dict[str, object] = Field(default_factory=dict)
    matchers: list[dict[str, object]] = Field(default_factory=list)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime = Field(alias="endsAt")
    created_by: str = Field(default="", alias="createdBy")
    comment: str = ""

    model_config = {"populate_by_name": True, "extra": "allow"}


class AlertmanagerStatus(BaseModel):
    """Represents the Alertmanager v2 cluster/version status payload (passthrough)."""

    cluster: dict[str, object] | None = None
    version_info: dict[str, object] | None = Field(default=None, alias="versionInfo")
    uptime: datetime | None = None

    model_config = {"populate_by_name": True, "extra": "allow"}


class MimirInstantSample(BaseModel):
    """Represents a single instant-vector sample from a Mimir Prometheus query."""

    metric: dict[str, str] = Field(default_factory=dict)
    timestamp: float
    value: str


class MimirInstantResult(BaseModel):
    """Represents the parsed response of a Mimir instant query."""

    result_type: str
    samples: list[MimirInstantSample] = Field(default_factory=list)


class AiHordeUser(BaseModel):
    """Represents the subset of the AI Horde find_user response we depend on."""

    id: int | None = None
    username: str | None = None
    moderator: bool = False

    model_config = {"extra": "allow"}
