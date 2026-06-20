"""Alembic migration environment.

Connects synchronously using the SQLAlchemy URL from
``HordeAlertsSettings.database_url`` (with the asyncpg driver swapped for
psycopg2 because Alembic runs synchronously).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from ai_horde_service_alerts.db.base import Base
from ai_horde_service_alerts.db.models import (  # noqa: F401 — needed for Alembic to see metadata
    Component,
    ComponentOverride,
    ComponentStatusHistory,
    Incident,
    IncidentComponent,
    IncidentUpdate,
    MaintenanceComponent,
    MaintenanceWindow,
    ProbeResult,
)
from ai_horde_service_alerts.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_sync_url() -> str:
    raw = get_settings().database_url
    return (
        raw.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("sqlite+aiosqlite://", "sqlite://")
    )


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render migrations against a URL without an engine."""
    context.configure(
        url=_resolve_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live SQLAlchemy connection."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolve_sync_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
