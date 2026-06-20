"""Webhooks reachability — checks the webhooks status surface."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import override

import httpx

from horde_status_prober.probes.base import Probe, ProbeOutcome, ProbeResult, ProbeResultDetail


class WebhooksSmokeProbe(Probe):
    """Pings the public webhooks status URL.

    The actual endpoint is configurable per environment via the
    ``aihorde_base_url`` setting; we deliberately avoid sending a live
    webhook payload since that would require state in the upstream API.
    """

    name = "webhooks_smoke"
    component_id = "webhooks"

    @override
    async def run(self, http: httpx.AsyncClient) -> ProbeResult:
        observed_at = datetime.now(tz=UTC)
        start = time.perf_counter()
        try:
            response = await http.get("/v2/status/heartbeat")
            latency_ms = int((time.perf_counter() - start) * 1000)
        except httpx.HTTPError as exc:
            return ProbeResult(
                probe_name=self.name,
                component_id=self.component_id,
                outcome=ProbeOutcome.DOWN,
                observed_at=observed_at,
                detail=ProbeResultDetail(error=exc.__class__.__name__, message=str(exc)),
            )

        if response.status_code != 200:
            return ProbeResult(
                probe_name=self.name,
                component_id=self.component_id,
                outcome=ProbeOutcome.DOWN,
                observed_at=observed_at,
                latency_ms=latency_ms,
                detail=ProbeResultDetail(status_code=response.status_code),
            )
        outcome = ProbeOutcome.OK if latency_ms < 5_000 else ProbeOutcome.DEGRADED
        return ProbeResult(
            probe_name=self.name,
            component_id=self.component_id,
            outcome=outcome,
            observed_at=observed_at,
            latency_ms=latency_ms,
        )
