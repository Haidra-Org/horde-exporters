"""Unit tests for ModeratorAuthGuard caching behaviour."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from ai_horde_service_alerts.auth import ModeratorAuthGuard
from ai_horde_service_alerts.clients.errors import UpstreamUnavailable
from ai_horde_service_alerts.models.internal import AiHordeUser


def _guard(client: Any, *, positive: int = 60, negative: int = 15) -> ModeratorAuthGuard:
    return ModeratorAuthGuard(
        client,
        positive_ttl_seconds=positive,
        negative_ttl_seconds=negative,
        max_entries=64,
    )


async def test_authenticate_missing_key_returns_401() -> None:
    guard = _guard(AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await guard.authenticate(None)
    assert exc.value.status_code == 401


async def test_authenticate_caches_positive_result() -> None:
    client = AsyncMock()
    client.find_user = AsyncMock(return_value=AiHordeUser(id=1, username="m", moderator=True))
    guard = _guard(client)

    identity = await guard.authenticate("apikey-1")
    assert identity.username == "m"

    # Second call must not hit upstream.
    again = await guard.authenticate("apikey-1")
    assert again.username == "m"
    assert client.find_user.await_count == 1


async def test_authenticate_caches_negative_result() -> None:
    client = AsyncMock()
    client.find_user = AsyncMock(return_value=AiHordeUser(id=2, username="u", moderator=False))
    guard = _guard(client)

    with pytest.raises(HTTPException) as first:
        await guard.authenticate("apikey-2")
    assert first.value.status_code == 403

    with pytest.raises(HTTPException) as second:
        await guard.authenticate("apikey-2")
    assert second.value.status_code == 403
    assert client.find_user.await_count == 1


async def test_authenticate_unknown_user_returns_403() -> None:
    client = AsyncMock()
    client.find_user = AsyncMock(return_value=None)
    guard = _guard(client)
    with pytest.raises(HTTPException) as exc:
        await guard.authenticate("apikey-3")
    assert exc.value.status_code == 403


async def test_authenticate_upstream_failure_returns_503() -> None:
    client = AsyncMock()
    client.find_user = AsyncMock(side_effect=UpstreamUnavailable("aihorde", "boom"))
    guard = _guard(client)
    with pytest.raises(HTTPException) as exc:
        await guard.authenticate("apikey-4")
    assert exc.value.status_code == 503
