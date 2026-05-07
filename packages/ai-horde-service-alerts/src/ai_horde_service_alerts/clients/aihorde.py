"""Async client for the AI Horde public REST API (subset)."""

from __future__ import annotations

import logging

import httpx

from ai_horde_service_alerts.clients.errors import UpstreamUnavailable
from ai_horde_service_alerts.models.internal import AiHordeUser

logger = logging.getLogger(__name__)


class AiHordeClient:
    """Subset of the AI Horde REST API used by the moderator auth guard."""

    def __init__(self, http_client: httpx.AsyncClient, *, client_agent: str) -> None:
        """Bind the client to an externally managed :class:`httpx.AsyncClient`."""
        self._http = http_client
        self._client_agent = client_agent

    async def find_user(self, api_key: str) -> AiHordeUser | None:
        """Return :class:`AiHordeUser` for the supplied API key, or None when unknown.

        Maps HTTP responses to outcomes:
          - 200: parsed user payload.
          - 401/404: ``None`` (treated as "key not associated with a user").
          - any other non-2xx: :class:`UpstreamUnavailable` so callers can
            return a 503 to clients without poisoning the auth cache with a
            misleading negative result.

        Args:
            api_key: AI Horde API key supplied by the caller.

        Raises:
            UpstreamUnavailable: When the AI Horde API returns a non-handled
                error or the request fails at the transport layer.
        """
        try:
            response = await self._http.get(
                "v2/find_user",
                headers={
                    "apikey": api_key,
                    "Client-Agent": self._client_agent,
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable("aihorde", str(exc)) from exc

        if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.NOT_FOUND):
            return None
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise UpstreamUnavailable(
                "aihorde",
                f"find_user -> {response.status_code}",
                status_code=response.status_code,
            )
        return AiHordeUser.model_validate(response.json())
