"""Settings module.

Centralizes configuration sourced from environment variables prefixed
``HORDE_ALERTS_`` (and optional ``.env``). Settings are immutable at runtime
and obtained via :func:`get_settings` which is cached for the process.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_COMPONENTS_PATH = PACKAGE_ROOT / "config" / "components.yaml"
DEFAULT_ALERT_MAP_PATH = PACKAGE_ROOT / "config" / "alert_component_map.yaml"


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
        default="ai-horde-service-alerts:0.2.0:haidra",
        description="Value for the AI Horde Client-Agent header.",
    )

    moderator_cache_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    moderator_cache_negative_ttl_seconds: int = Field(default=15, ge=1, le=3600)
    moderator_cache_max_entries: int = Field(default=1024, ge=16, le=65_536)

    cors_allow_origins: list[str] = Field(default_factory=list)
    request_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)

    enable_internal_swagger_docs: bool = Field(
        default=True,
        description="Expose /docs and /redoc. Disable for purely public-facing deployments.",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://horde_status:horde_status@127.0.0.1:5432/horde_status",
        description="SQLAlchemy URL for the status-page Postgres database.",
    )
    database_echo: bool = Field(
        default=False,
        description="Emit SQL statements to the logger (for debugging only).",
    )
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=200)

    components_config_path: Path = DEFAULT_COMPONENTS_PATH
    alert_component_map_path: Path = DEFAULT_ALERT_MAP_PATH

    prober_shared_secret: SecretStr | None = Field(
        default=None,
        description="Shared secret accepted on POST /internal/probe-results. None disables push.",
    )

    status_evaluator_interval_seconds: float = Field(default=15.0, gt=0.0, le=300.0)
    no_signal_grace_seconds: int = Field(
        default=900,
        ge=0,
        le=86_400,
        description="Grace period before no-signal components become UNKNOWN.",
    )
    maintenance_runner_interval_seconds: float = Field(default=30.0, gt=0.0, le=600.0)
    history_retention_days: int = Field(default=400, ge=90, le=3650)
    probe_result_retention_days: int = Field(default=7, ge=1, le=90)

    enable_background_tasks: bool = Field(
        default=True,
        description="Run the in-process status_evaluator and maintenance_runner. Disable in tests.",
    )
    enable_db: bool = Field(
        default=True,
        description="Connect to Postgres on startup. Disable in unit tests that mock all data paths.",
    )

    backfill_on_startup: bool = Field(
        default=False,
        description="Run the Mimir backfill at startup. Set true once after first deploy, then false.",
    )
    backfill_window_days: int = Field(default=90, ge=1, le=400)


@lru_cache
def get_settings() -> HordeAlertsSettings:
    """Return the cached :class:`HordeAlertsSettings` for the current process."""
    return HordeAlertsSettings()
