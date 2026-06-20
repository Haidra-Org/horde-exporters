"""Common types shared by all probes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict


class ProbeResultDetail(BaseModel):
    """Represents the explicit optional detail fields attached to one probe sample."""

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


class ProbePayload(BaseModel):
    """Represents the JSON body accepted by ``/internal/probe-results``."""

    model_config = ConfigDict(extra="forbid")

    probe_name: str
    component_id: str
    outcome: str
    observed_at: str
    latency_ms: int | None = None
    detail: ProbeResultDetail | None = None


class ProbeOutcome(StrEnum):
    """Outcome buckets accepted by the alerts service.

    Must stay in sync with the server-side enum
    ``ai_horde_service_alerts.db.types.ProbeOutcome``.
    """

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(slots=True)
class ProbeResult:
    """One probe sample, ready to be POSTed to ``/internal/probe-results``."""

    probe_name: str
    component_id: str
    outcome: ProbeOutcome
    observed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    latency_ms: int | None = None
    detail: ProbeResultDetail | None = None

    def to_payload(self) -> ProbePayload:
        """Render as the JSON body the alerts service expects."""
        return ProbePayload(
            probe_name=self.probe_name,
            component_id=self.component_id,
            outcome=self.outcome.value,
            observed_at=self.observed_at.astimezone(UTC).isoformat(),
            latency_ms=self.latency_ms,
            detail=self.detail,
        )


class Probe(Protocol):
    """One blackbox probe.

    Implementations MUST be tolerant of any upstream failure (timeouts,
    connection refused, malformed JSON, …) and return a
    :class:`ProbeResult` with ``outcome=DOWN`` rather than raising.
    """

    name: str
    component_id: str

    async def run(self, http: httpx.AsyncClient) -> ProbeResult:
        """Execute the probe against the supplied HTTP client."""
        ...
