"""Alchemy worker availability — same shape as image/text probes."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import override

import httpx

from horde_status_prober.probes.base import Probe, ProbeOutcome, ProbeResult, ProbeResultDetail

DEGRADED_BELOW = 2
DOWN_BELOW = 1


class AlchemySmokeProbe(Probe):
    """Checks the alchemy worker pool via ``/v2/workers?type=interrogation``."""

    name = "alchemy_smoke"
    component_id = "alchemy"

    def __init__(self, *, degraded_below: int = DEGRADED_BELOW, down_below: int = DOWN_BELOW) -> None:
        """Grade DEGRADED below ``degraded_below`` online workers, DOWN below ``down_below``."""
        self._degraded_below = degraded_below
        self._down_below = down_below

    @override
    async def run(self, http: httpx.AsyncClient) -> ProbeResult:
        observed_at = datetime.now(tz=UTC)
        start = time.perf_counter()
        latency_ms = None
        try:
            response = await http.get("/v2/workers", params={"type": "interrogation"})
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
            payload = response.json()
        except httpx.HTTPError as exc:
            return ProbeResult(
                probe_name=self.name,
                component_id=self.component_id,
                outcome=ProbeOutcome.DOWN,
                observed_at=observed_at,
                detail=ProbeResultDetail(error=exc.__class__.__name__, message=str(exc)),
            )
        except ValueError as exc:
            return ProbeResult(
                probe_name=self.name,
                component_id=self.component_id,
                outcome=ProbeOutcome.DEGRADED,
                observed_at=observed_at,
                latency_ms=latency_ms,
                detail=ProbeResultDetail(parse_error=str(exc)),
            )

        if not isinstance(payload, list):
            return ProbeResult(
                probe_name=self.name,
                component_id=self.component_id,
                outcome=ProbeOutcome.DEGRADED,
                observed_at=observed_at,
                latency_ms=latency_ms,
                detail=ProbeResultDetail(reason="non-list payload"),
            )
        online = sum(
            1 for worker in payload if isinstance(worker, dict) and worker.get("online") is True
        )
        if online < self._down_below:
            outcome = ProbeOutcome.DOWN
        elif online < self._degraded_below:
            outcome = ProbeOutcome.DEGRADED
        else:
            outcome = ProbeOutcome.OK
        return ProbeResult(
            probe_name=self.name,
            component_id=self.component_id,
            outcome=outcome,
            observed_at=observed_at,
            latency_ms=latency_ms,
            detail=ProbeResultDetail(online_workers=online, total_workers=len(payload)),
        )
