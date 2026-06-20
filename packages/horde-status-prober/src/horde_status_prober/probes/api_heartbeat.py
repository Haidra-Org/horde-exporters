"""``GET /v2/status/heartbeat`` — basic API liveness."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import override

import httpx

from horde_status_prober.probes.base import Probe, ProbeOutcome, ProbeResult, ProbeResultDetail


class ApiHeartbeatProbe(Probe):
    """Hits the heartbeat endpoint and grades on HTTP status + latency."""

    name = "api_heartbeat"
    component_id = "api"

    @override
    async def run(self, http: httpx.AsyncClient) -> ProbeResult:
        observed_at = datetime.now(tz=UTC)
        start = time.perf_counter()
        try:
            response = await http.get("/v2/status/heartbeat")
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
            outcome = ProbeOutcome.OK if latency_ms < 2_000 else ProbeOutcome.DEGRADED
            return ProbeResult(
                probe_name=self.name,
                component_id=self.component_id,
                outcome=outcome,
                observed_at=observed_at,
                latency_ms=latency_ms,
            )
        except httpx.HTTPError as exc:
            return ProbeResult(
                probe_name=self.name,
                component_id=self.component_id,
                outcome=ProbeOutcome.DOWN,
                observed_at=observed_at,
                detail=ProbeResultDetail(error=exc.__class__.__name__, message=str(exc)),
            )
