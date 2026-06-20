"""Real-Postgres Alembic migration parity test.

The rest of the suite builds the schema from ``Base.metadata`` on SQLite and
never runs Alembic — so the production migration (Postgres-native enums, the
``postgresql`` dialect) is otherwise unexercised. This test runs
``alembic upgrade head`` against a real Postgres and asserts the resulting
schema matches the ORM models (no missing/extra tables or columns), catching
model<->migration drift before it reaches a deployment.

It is skipped unless ``HORDE_ALERTS_TEST_DATABASE_URL`` points at a Postgres
database. Locally:

    docker run --rm -d --name pg-alembic-test -e POSTGRES_PASSWORD=pw \
        -e POSTGRES_DB=horde_status -p 5433:5432 postgres:16-alpine
    HORDE_ALERTS_TEST_DATABASE_URL=postgresql+asyncpg://postgres:pw@127.0.0.1:5433/horde_status \
        uv run pytest tests/integration/test_migrations.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from ai_horde_service_alerts.db import models  # noqa: F401 — populate Base.metadata
from ai_horde_service_alerts.db.base import Base
from ai_horde_service_alerts.settings import get_settings

_PG_URL = os.environ.get("HORDE_ALERTS_TEST_DATABASE_URL", "")
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]

# compare_metadata op codes that signal genuine schema drift (vs. cosmetic
# type/default representation differences that are noisy across dialects).
_STRUCTURAL_OPS = {"add_table", "remove_table", "add_column", "remove_column"}

pytestmark = pytest.mark.skipif(
    not _PG_URL.startswith("postgresql"),
    reason="Set HORDE_ALERTS_TEST_DATABASE_URL to a Postgres URL to run the migration parity test.",
)


def _sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def test_migrations_apply_and_match_models(monkeypatch: pytest.MonkeyPatch) -> None:
    # Alembic's env.py resolves the URL from get_settings().database_url, so
    # point settings at the test Postgres and clear the process-wide cache.
    monkeypatch.setenv("HORDE_ALERTS_DATABASE_URL", _PG_URL)
    get_settings.cache_clear()

    sync_engine = create_engine(_sync_url(_PG_URL), future=True)
    try:
        # Start from a clean schema so the run is repeatable.
        with sync_engine.begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")

        cfg = Config(str(_PACKAGE_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(_PACKAGE_ROOT / "migrations"))
        command.upgrade(cfg, "head")

        # Every model table must have been created by the migration.
        present = set(inspect(sync_engine).get_table_names())
        expected = set(Base.metadata.tables) | {"alembic_version"}
        missing = expected - present
        assert not missing, f"migration did not create tables: {sorted(missing)}"

        # No structural drift between the migrated schema and the ORM models.
        with sync_engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn,
                opts={"compare_type": True, "compare_server_default": True},
            )
            diff = compare_metadata(ctx, Base.metadata)
    finally:
        sync_engine.dispose()
        get_settings.cache_clear()

    structural = [op for op in _op_codes(diff) if op in _STRUCTURAL_OPS]
    assert not structural, f"model<->migration structural drift detected: {structural}\nfull diff: {diff}"


def _op_codes(diff: object) -> list[str]:
    """Flatten compare_metadata output to its op-code strings.

    Entries are either a tuple whose first element is the op code (table-level
    diffs) or a list of such tuples (grouped column-level diffs).
    """
    codes: list[str] = []
    for entry in diff if isinstance(diff, list) else []:
        if isinstance(entry, list):
            codes.extend(sub[0] for sub in entry if isinstance(sub, (tuple, list)) and sub and isinstance(sub[0], str))
        elif isinstance(entry, (tuple, list)) and entry and isinstance(entry[0], str):
            codes.append(entry[0])
    return codes
