"""Settings module.

Centralizes configuration sourced from environment variables prefixed
``HORDE_ALERTS_`` (and optional ``.env``). Settings are immutable at runtime
and obtained via :func:`get_settings` which is cached for the process.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class HordeAlertsSettings(BaseSettings):
    """Represents runtime configuration for the service-alerts middleman."""

    model_config = SettingsConfigDict(
        env_prefix="HORDE_ALERTS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    alertmanager_base_url: HttpUrl = Field(
        default=HttpUrl("http://127.0.0.1:9093"),
        description="Alertmanager base URL (no trailing path component).",
    )
    mimir_base_url: HttpUrl = Field(
        default=HttpUrl("http://127.0.0.1:9009"),
        description="Mimir base URL (root, NOT /prometheus or /alertmanager).",
    )
    mimir_tenant_default: str = Field(
        default="ai-horde-public",
        description="Default Mimir tenant header (X-Scope-OrgID) for curated public queries.",
    )
    mimir_curated_queries: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Mapping of component name to PromQL instant query that returns 1 (healthy) or 0 "
            "(degraded). Parsed from JSON in env var HORDE_ALERTS_MIMIR_CURATED_QUERIES."
        ),
    )

    upstream_basic_auth_user: str = Field(default="", description="HTTP basic auth user for upstream calls.")
    upstream_basic_auth_password: SecretStr | None = Field(
        default=None,
        description="HTTP basic auth password (paired with upstream_basic_auth_user).",
    )

    aihorde_base_url: HttpUrl = Field(
        default=HttpUrl("https://aihorde.net/api/"),
        description="AI Horde API base URL (must end with /api/ and a trailing slash).",
    )
    aihorde_client_agent: str = Field(
        default="ai-horde-service-alerts:0.1.0:haidra",
        description="Value for the AI Horde Client-Agent header.",
    )

    moderator_cache_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    moderator_cache_negative_ttl_seconds: int = Field(default=15, ge=1, le=3600)
    moderator_cache_max_entries: int = Field(default=1024, ge=16, le=65_536)

    public_alert_label_allowlist: frozenset[str] = Field(
        default=frozenset({"severity", "component", "service", "alertname"}),
        description="Label keys retained when projecting alerts to public consumers.",
    )
    public_annotation_allowlist: frozenset[str] = Field(
        default=frozenset({"summary"}),
        description="Annotation keys retained when projecting alerts to public consumers.",
    )

    cors_allow_origins: list[str] = Field(default_factory=list)
    request_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)

    enable_internal_swagger_docs: bool = Field(
        default=True,
        description="Expose /docs and /redoc. Disable for purely public-facing deployments.",
    )


@lru_cache
def get_settings() -> HordeAlertsSettings:
    """Return the cached :class:`HordeAlertsSettings` for the current process."""
    return HordeAlertsSettings()
