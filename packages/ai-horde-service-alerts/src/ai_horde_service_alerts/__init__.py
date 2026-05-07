"""AI Horde service-alerts middleman FastAPI service.

Public package exposing :func:`create_app` for ASGI servers and tests.
"""

from __future__ import annotations

from ai_horde_service_alerts.app import create_app

__all__ = ["create_app"]
