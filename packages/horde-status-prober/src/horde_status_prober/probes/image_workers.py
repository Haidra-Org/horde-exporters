"""Image worker count — degrades the ``image`` component when the pool drains."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, override

import httpx

from horde_status_prober.probes.base import Probe, ProbeOutcome, ProbeResult, ProbeResultDetail

# Thresholds chosen to match the ``HordeImageWorkerCountDrop`` alert in the
# alerts service's curated map. Override at deploy time if the volunteer
# pool size changes meaningfully.
DEGRADED_BELOW = 5
DOWN_BELOW = 1


class ImageWorkersProbe(Probe):
    """Reads the ``performance`` snapshot to decide image-pool health."""

    name = "image_workers"
    component_id = "image"

    @override
    async def run(self, http: httpx.AsyncClient) -> ProbeResult:
        observed_at = datetime.now(tz=UTC)
        start = time.perf_counter()
        latency_ms = None
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

        worker_count = (
            _coerce_int(payload.get("worker_count")) if isinstance(payload, dict) else None
        )
        if worker_count is None:
            return ProbeResult(
                probe_name=self.name,
                component_id=self.component_id,
                outcome=ProbeOutcome.DEGRADED,
                observed_at=observed_at,
                latency_ms=latency_ms,
                detail=ProbeResultDetail(reason="worker_count missing"),
            )
        if worker_count < DOWN_BELOW:
            outcome = ProbeOutcome.DOWN
        elif worker_count < DEGRADED_BELOW:
            outcome = ProbeOutcome.DEGRADED
        else:
            outcome = ProbeOutcome.OK
        return ProbeResult(
            probe_name=self.name,
            component_id=self.component_id,
            outcome=outcome,
            observed_at=observed_at,
            latency_ms=latency_ms,
            detail=ProbeResultDetail(worker_count=worker_count),
        )


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
