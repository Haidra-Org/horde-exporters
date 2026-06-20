"""Authentication guard and moderator-status caching."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from cachetools import TTLCache
from fastapi import HTTPException, status

from ai_horde_service_alerts.clients.aihorde import AiHordeClient
from ai_horde_service_alerts.clients.errors import UpstreamUnavailable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModeratorIdentity:
    """Represents an authenticated moderator's identity passed to handlers."""

    username: str
    user_id: int | None


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    """Internal cache entry capturing positive or negative auth outcomes."""

    is_moderator: bool
    identity: ModeratorIdentity | None


class ModeratorAuthGuard:
    """Validate ``apikey`` headers against the AI Horde find_user endpoint.

    Both positive and negative results are cached in TTL caches keyed by a
    SHA-256 of the API key so that repeated calls neither hammer AI Horde nor
    leak the raw key into memory long-term. Negative results use a shorter
    TTL to limit damage from stolen keys whose moderator status was revoked.
    """

    def __init__(
        self,
        aihorde_client: AiHordeClient,
        *,
        positive_ttl_seconds: int,
        negative_ttl_seconds: int,
        max_entries: int,
    ) -> None:
        """Construct the auth guard with the supplied AI Horde client + cache config."""
        self._client = aihorde_client
        self._positive_cache: TTLCache[str, _CacheEntry] = TTLCache(
            maxsize=max_entries,
            ttl=positive_ttl_seconds,
        )
        self._negative_cache: TTLCache[str, _CacheEntry] = TTLCache(
            maxsize=max_entries,
            ttl=negative_ttl_seconds,
        )

    def invalidate(self) -> None:
        """Clear all cached auth outcomes. Intended for tests and admin reload."""
        self._positive_cache.clear()
        self._negative_cache.clear()

    async def authenticate(self, api_key: str | None) -> ModeratorIdentity:
        """Return the :class:`ModeratorIdentity` for ``api_key`` or raise HTTPException.

        Raises:
            HTTPException: ``401`` when the header is missing/blank, ``403``
                when the key does not belong to a moderator, ``503`` when the
                AI Horde API is unreachable.
        """
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing apikey header.",
                headers={"WWW-Authenticate": 'ApiKey realm="ai-horde-service-alerts"'},
            )

        cache_key = _hash_api_key(api_key)
        cached = self._positive_cache.get(cache_key) or self._negative_cache.get(cache_key)
        if cached is not None:
            return _enforce(cached)

        try:
            user = await self._client.find_user(api_key)
        except UpstreamUnavailable as exc:
            logger.warning("AI Horde find_user upstream failure: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI Horde authentication backend is unavailable.",
            ) from exc

        if user is None or not user.moderator:
            entry = _CacheEntry(is_moderator=False, identity=None)
            self._negative_cache[cache_key] = entry
            return _enforce(entry)

        entry = _CacheEntry(
            is_moderator=True,
            identity=ModeratorIdentity(username=user.username or "", user_id=user.id),
        )
        self._positive_cache[cache_key] = entry
        return _enforce(entry)


def _enforce(entry: _CacheEntry) -> ModeratorIdentity:
    if not entry.is_moderator or entry.identity is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key is not associated with a moderator account.",
        )
    return entry.identity


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()
