"""HTTP client that POSTs probe results into the alerts service.

Uses the shared-secret header (``x-prober-secret``) accepted by
``ai_horde_service_alerts.dependencies.require_prober_secret``.
"""

from __future__ import annotations

import logging

import httpx

from horde_status_prober.config import ProberSettings
from horde_status_prober.probes.base import ProbeResult

_logger = logging.getLogger(__name__)


class AlertsPusher:
    """Submits :class:`ProbeResult` instances to the alerts service."""

    def __init__(self, settings: ProberSettings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._consecutive_failures = 0

    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive failed pushes since the last success."""
        return self._consecutive_failures

    @property
    def is_unhealthy(self) -> bool:
        """``True`` when too many consecutive pushes have failed."""
        return self._consecutive_failures >= self._settings.max_consecutive_push_failures

    async def push(self, result: ProbeResult) -> bool:
        """POST ``result`` to ``/internal/probe-results``.

        Returns ``True`` on a 2xx response, ``False`` otherwise.
        """
        url = f"{self._settings.alerts_base_url.rstrip('/')}/internal/probe-results"
        headers = {
            "x-prober-secret": self._settings.prober_shared_secret.get_secret_value(),
            "content-type": "application/json",
            "user-agent": self._settings.user_agent,
        }
        try:
            response = await self._client.post(
                url,
                json=result.to_payload().model_dump(exclude_none=True),
                headers=headers,
                timeout=self._settings.alerts_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            self._consecutive_failures += 1
            _logger.warning(
                "Probe push failed (transport error): probe=%s component=%s error=%s",
                result.probe_name,
                result.component_id,
                exc,
            )
            return False

        if 200 <= response.status_code < 300:
            self._consecutive_failures = 0
            _logger.debug(
                "Pushed probe result probe=%s component=%s outcome=%s status=%s",
                result.probe_name,
                result.component_id,
                result.outcome.value,
                response.status_code,
            )
            return True

        self._consecutive_failures += 1
        _logger.warning(
            "Probe push rejected: probe=%s component=%s status=%s body=%s",
            result.probe_name,
            result.component_id,
            response.status_code,
            response.text[:200],
        )
        return False
