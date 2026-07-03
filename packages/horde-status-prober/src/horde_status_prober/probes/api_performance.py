"""``GET /v2/status/performance`` — drives API-tier latency grading."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import override

import httpx

from horde_status_prober.probes.base import Probe, ProbeOutcome, ProbeResult, ProbeResultDetail


#: Latency (ms) at or above which a parseable 200 response is graded DEGRADED.
DEFAULT_DEGRADED_MS = 3_000


class ApiPerformanceProbe(Probe):
    """Performance endpoint should respond quickly and parse as JSON."""

    name = "api_performance"
    component_id = "api"

    def __init__(self, *, degraded_ms: int = DEFAULT_DEGRADED_MS) -> None:
        """Grade a parseable 200 as DEGRADED when latency reaches ``degraded_ms``."""
        self._degraded_ms = degraded_ms

    @override
    async def run(self, http: httpx.AsyncClient) -> ProbeResult:
        observed_at = datetime.now(tz=UTC)
        start = time.perf_counter()
        try:
            response = await http.get("/v2/status/performance")
            latency_ms = int((time.perf_counter() - start) * 1000)
            if response.status_code != 200:
                return ProbeResult(
                    probe_name=self.name,
                    component_id=self.component_id,
                    outcome=ProbeOutcome.DOWN,
                    observed_at=observed_at,
                    latency_ms=latency_ms,
                    detail=ProbeResultDetail(status_code=response.status_code),
                )
            try:
                payload = response.json()
            except ValueError as exc:
                return ProbeResult(
                    probe_name=self.name,
                    component_id=self.component_id,
                    outcome=ProbeOutcome.DEGRADED,
                    observed_at=observed_at,
                    latency_ms=latency_ms,
                    detail=ProbeResultDetail(parse_error=str(exc)),
                )
            outcome = ProbeOutcome.OK if latency_ms < self._degraded_ms else ProbeOutcome.DEGRADED
            return ProbeResult(
                probe_name=self.name,
                component_id=self.component_id,
                outcome=outcome,
                observed_at=observed_at,
                latency_ms=latency_ms,
                detail=ProbeResultDetail(
                    keys=sorted(payload.keys()) if isinstance(payload, dict) else None
                ),
            )
        except httpx.HTTPError as exc:
            return ProbeResult(
                probe_name=self.name,
                component_id=self.component_id,
                outcome=ProbeOutcome.DOWN,
                observed_at=observed_at,
                detail=ProbeResultDetail(error=exc.__class__.__name__, message=str(exc)),
            )
