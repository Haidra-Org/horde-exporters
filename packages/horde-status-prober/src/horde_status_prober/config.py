"""Environment-driven configuration for the prober.

All settings are read from environment variables prefixed ``HORDE_PROBER_``
(or any of the secondary aliases). The shared secret used to authenticate
against ``/internal/probe-results`` MUST match the alerts service's
``prober_shared_secret`` setting.
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProberSettings(BaseSettings):
    """Container for runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="HORDE_PROBER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    aihorde_base_url: str = Field(
        default="https://aihorde.net/api",
        description="Base URL of the public AI Horde API to probe.",
    )
    alerts_base_url: str = Field(
        default="https://alerts.haidra.net/api/v1",
        description="Base URL of the ai-horde-service-alerts service.",
    )
    prober_shared_secret: SecretStr = Field(
        description="Shared secret sent in the x-prober-secret header.",
    )

    api_heartbeat_interval: int = 30
    api_performance_interval: int = 60
    image_workers_interval: int = 60
    text_workers_interval: int = 60
    webhooks_smoke_interval: int = 300
    alchemy_smoke_interval: int = 300

    healthz_host: str = "0.0.0.0"
    healthz_port: int = 8081

    aihorde_timeout_seconds: float = 10.0
    alerts_timeout_seconds: float = 10.0

    user_agent: str = "horde-status-prober/0.1"
    max_consecutive_push_failures: int = 5
