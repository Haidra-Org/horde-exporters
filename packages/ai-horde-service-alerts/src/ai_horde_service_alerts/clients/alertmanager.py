"""Async client for the Alertmanager v2 HTTP API."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import TypeAdapter

from ai_horde_service_alerts.clients.errors import UpstreamUnavailable
from ai_horde_service_alerts.models.internal import (
    AlertmanagerAlert,
    AlertmanagerSilence,
    AlertmanagerStatus,
)

logger = logging.getLogger(__name__)

_ALERTS_ADAPTER: TypeAdapter[list[AlertmanagerAlert]] = TypeAdapter(list[AlertmanagerAlert])
_SILENCES_ADAPTER: TypeAdapter[list[AlertmanagerSilence]] = TypeAdapter(list[AlertmanagerSilence])


class AlertmanagerClient:
    """Thin typed wrapper around the Alertmanager v2 HTTP API.

    The instance does not own the underlying :class:`httpx.AsyncClient`; the
    application lifespan owns the client lifecycle so that connection pooling
    is shared across requests.
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        """Bind the client to an externally managed :class:`httpx.AsyncClient`."""
        self._http = http_client

    async def list_alerts(
        self,
        *,
        active: bool = True,
        silenced: bool = False,
        inhibited: bool = False,
    ) -> list[AlertmanagerAlert]:
        """Return alerts from ``GET /api/v2/alerts``."""
        params: dict[str, str] = {
            "active": _bool(active),
            "silenced": _bool(silenced),
            "inhibited": _bool(inhibited),
        }
        payload = await self._get_json("/api/v2/alerts", params=params)
        if not isinstance(payload, list):
            raise UpstreamUnavailable("alertmanager", "expected list payload from /api/v2/alerts")
        return _ALERTS_ADAPTER.validate_python(payload)

    async def list_silences(self) -> list[AlertmanagerSilence]:
        """Return silences from ``GET /api/v2/silences``."""
        payload = await self._get_json("/api/v2/silences")
        if not isinstance(payload, list):
            raise UpstreamUnavailable("alertmanager", "expected list payload from /api/v2/silences")
        return _SILENCES_ADAPTER.validate_python(payload)

    async def get_status(self) -> AlertmanagerStatus:
        """Return cluster/version status from ``GET /api/v2/status``."""
        payload = await self._get_json("/api/v2/status")
        if not isinstance(payload, dict):
            raise UpstreamUnavailable("alertmanager", "expected object payload from /api/v2/status")
        return AlertmanagerStatus.model_validate(payload)

    async def is_ready(self) -> bool:
        """Return True if Alertmanager reports readiness via ``/-/ready``."""
        try:
            response = await self._http.get("/-/ready")
        except httpx.HTTPError as exc:
            logger.warning("alertmanager readiness probe failed: %s", exc)
            return False
        return response.status_code == httpx.codes.OK

    async def _get_json(self, path: str, *, params: dict[str, str] | None = None) -> Any:  # noqa: ANN401 - JSON
        try:
            response = await self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable("alertmanager", str(exc)) from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise UpstreamUnavailable(
                "alertmanager",
                f"GET {path} -> {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()


def _bool(value: bool) -> str:
    return "true" if value else "false"
