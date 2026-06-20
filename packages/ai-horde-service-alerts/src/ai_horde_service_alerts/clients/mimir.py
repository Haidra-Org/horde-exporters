"""Async client for the Mimir Prometheus-compatible query API."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ai_horde_service_alerts.clients.errors import UpstreamUnavailable
from ai_horde_service_alerts.models.internal import (
    MimirInstantResult,
    MimirInstantSample,
    MimirRangeResult,
    MimirRangeSeries,
)

logger = logging.getLogger(__name__)


_MAX_QUERY_LENGTH = 4096
_QUERY_DENYLIST = re.compile(r"[;\x00\r\n]")


class MimirClient:
    """Thin typed wrapper around Mimir's Prometheus-compatible read API."""

    def __init__(self, http_client: httpx.AsyncClient, *, default_tenant: str) -> None:
        """Bind the client to an externally managed :class:`httpx.AsyncClient`.

        Args:
            http_client: Shared async HTTP client whose ``base_url`` points at
                the Mimir root (no ``/prometheus`` suffix).
            default_tenant: Default tenant id to use for the
                ``X-Scope-OrgID`` header when callers omit it.
        """
        self._http = http_client
        self._default_tenant = default_tenant

    async def query_instant(
        self,
        query: str,
        *,
        tenant: str | None = None,
    ) -> MimirInstantResult:
        """Return the parsed result of an instant Prometheus query against Mimir.

        Args:
            query: PromQL expression. Validated for safe characters and length.
            tenant: Override for the ``X-Scope-OrgID`` tenant header.

        Raises:
            UpstreamUnavailable: When Mimir returns a non-2xx response or the
                request fails at the transport layer.
            ValueError: When the supplied query is empty, oversized, or
                contains disallowed characters.
        """
        validated = _validate_query(query)
        scope_tenant = tenant or self._default_tenant
        try:
            response = await self._http.get(
                "/prometheus/api/v1/query",
                params={"query": validated},
                headers={"X-Scope-OrgID": scope_tenant},
            )
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable("mimir", str(exc)) from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise UpstreamUnavailable(
                "mimir",
                f"query {validated!r} -> {response.status_code}",
                status_code=response.status_code,
            )
        return _parse_instant(response.json())

    async def query_range(
        self,
        query: str,
        *,
        start: float,
        end: float,
        step: float,
        tenant: str | None = None,
    ) -> MimirRangeResult:
        """Run a Prometheus range query against Mimir and return parsed series.

        Args:
            query: PromQL expression. Validated like :meth:`query_instant`.
            start: Unix timestamp (seconds) for the start of the range.
            end: Unix timestamp (seconds) for the end of the range.
            step: Step duration in seconds.
            tenant: Override for the ``X-Scope-OrgID`` header.

        Raises:
            UpstreamUnavailable: On non-2xx response or transport failure.
            ValueError: On invalid query / range parameters.
        """
        validated = _validate_query(query)
        if step <= 0:
            raise ValueError("step must be positive")
        if end <= start:
            raise ValueError("end must be greater than start")
        scope_tenant = tenant or self._default_tenant
        try:
            response = await self._http.get(
                "/prometheus/api/v1/query_range",
                params={
                    "query": validated,
                    "start": f"{start:.3f}",
                    "end": f"{end:.3f}",
                    "step": f"{step:g}s",
                },
                headers={"X-Scope-OrgID": scope_tenant},
            )
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable("mimir", str(exc)) from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise UpstreamUnavailable(
                "mimir",
                f"range query {validated!r} -> {response.status_code}",
                status_code=response.status_code,
            )
        return _parse_range(response.json())

    async def is_ready(self) -> bool:
        """Return True when Mimir reports readiness via ``/ready``."""
        try:
            response = await self._http.get("/ready")
        except httpx.HTTPError as exc:
            logger.warning("mimir readiness probe failed: %s", exc)
            return False
        return response.status_code == httpx.codes.OK


def _validate_query(query: str) -> str:
    stripped = query.strip()
    if not stripped:
        raise ValueError("PromQL query must not be empty")
    if len(stripped) > _MAX_QUERY_LENGTH:
        raise ValueError(f"PromQL query exceeds max length of {_MAX_QUERY_LENGTH} chars")
    if _QUERY_DENYLIST.search(stripped):
        raise ValueError("PromQL query contains disallowed characters")
    return stripped


def _parse_instant(payload: Any) -> MimirInstantResult:  # noqa: ANN401 - JSON shape
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise UpstreamUnavailable("mimir", "instant query did not return status=success")
    data: dict[str, Any] = payload.get("data") or {}
    result_type = str(data.get("resultType", "vector"))
    raw_samples: list[Any] = data.get("result") or []
    samples: list[MimirInstantSample] = []
    for raw in raw_samples:
        metric: dict[str, Any] = raw.get("metric") or {}
        value = raw.get("value") or [0.0, "0"]
        samples.append(
            MimirInstantSample(
                metric={str(k): str(v) for k, v in metric.items()},
                timestamp=float(value[0]),
                value=str(value[1]),
            ),
        )
    return MimirInstantResult(result_type=result_type, samples=samples)


def _parse_range(payload: Any) -> MimirRangeResult:  # noqa: ANN401 - JSON shape
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise UpstreamUnavailable("mimir", "range query did not return status=success")
    data: dict[str, Any] = payload.get("data") or {}
    raw_series: list[Any] = data.get("result") or []
    series: list[MimirRangeSeries] = []
    for raw in raw_series:
        metric: dict[str, Any] = raw.get("metric") or {}
        raw_values: list[Any] = raw.get("values") or []
        values: list[tuple[float, str]] = []
        for pair in raw_values:
            if not isinstance(pair, list) or len(pair) < 2:
                continue
            values.append((float(pair[0]), str(pair[1])))
        series.append(
            MimirRangeSeries(
                metric={str(k): str(v) for k, v in metric.items()},
                values=values,
            ),
        )
    return MimirRangeResult(series=series)
