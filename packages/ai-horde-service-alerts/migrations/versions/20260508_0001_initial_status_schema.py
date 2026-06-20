"""Initial status schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-08

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# NOTE: ``create_type=False`` is essential. These named enums are shared by
# multiple tables; if SQLAlchemy auto-emitted ``CREATE TYPE`` during each
# ``op.create_table`` it would fail with "type already exists" on the second
# table. Instead we create each type exactly once upfront (``enum.create``
# below) and drop it explicitly in ``downgrade``.
_AUDIENCE = postgresql.ENUM("public", "internal", name="audience", create_type=False)
_STATUS = postgresql.ENUM(
    "operational",
    "degraded",
    "partial",
    "down",
    "maintenance",
    "unknown",
    name="component_status",
    create_type=False,
)
_HISTORY_SOURCE = postgresql.ENUM(
    "prober",
    "alerts",
    "override",
    "maintenance",
    "initial",
    "backfill",
    name="history_source",
    create_type=False,
)
_INCIDENT_SEVERITY = postgresql.ENUM(
    "info", "minor", "major", "critical", name="incident_severity", create_type=False,
)
_INCIDENT_STATUS = postgresql.ENUM(
    "investigating",
    "identified",
    "monitoring",
    "resolved",
    name="incident_status",
    create_type=False,
)
_PROBE_OUTCOME = postgresql.ENUM("ok", "degraded", "down", name="probe_outcome", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        for enum in (
            _AUDIENCE,
            _STATUS,
            _HISTORY_SOURCE,
            _INCIDENT_SEVERITY,
            _INCIDENT_STATUS,
            _PROBE_OUTCOME,
        ):
            enum.create(bind, checkfirst=True)

    audience_col = _AUDIENCE if is_postgres else sa.String(16)
    status_col = _STATUS if is_postgres else sa.String(16)
    history_source_col = _HISTORY_SOURCE if is_postgres else sa.String(16)
    incident_severity_col = _INCIDENT_SEVERITY if is_postgres else sa.String(16)
    incident_status_col = _INCIDENT_STATUS if is_postgres else sa.String(16)
    probe_outcome_col = _PROBE_OUTCOME if is_postgres else sa.String(16)
    uuid_col = postgresql.UUID(as_uuid=True) if is_postgres else sa.String(36)
    json_col = postgresql.JSONB if is_postgres else sa.JSON

    op.create_table(
        "components",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("audience", audience_col, nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "component_status_history",
        sa.Column("id", uuid_col, primary_key=True),
        sa.Column("component_id", sa.String(64), nullable=False),
        sa.Column("status", status_col, nullable=False),
        sa.Column("source", history_source_col, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("triggered_by", json_col(), nullable=True),
        sa.ForeignKeyConstraint(
            ["component_id"], ["components.id"], ondelete="CASCADE",
            name=op.f("fk_component_status_history_component_id_components"),
        ),
    )
    op.create_index(
        "ix_component_status_history_component_started",
        "component_status_history",
        ["component_id", "started_at"],
    )
    if is_postgres:
        op.create_index(
            "ix_component_status_history_open",
            "component_status_history",
            ["component_id"],
            unique=True,
            postgresql_where=sa.text("ended_at IS NULL"),
        )

    op.create_table(
        "component_overrides",
        sa.Column("id", uuid_col, primary_key=True),
        sa.Column("component_id", sa.String(64), nullable=False),
        sa.Column("target_status", status_col, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_by", sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(
            ["component_id"], ["components.id"], ondelete="CASCADE",
            name=op.f("fk_component_overrides_component_id_components"),
        ),
    )
    if is_postgres:
        op.create_index(
            "ix_component_overrides_active",
            "component_overrides",
            ["component_id"],
            unique=True,
            postgresql_where=sa.text("cleared_at IS NULL"),
        )

    op.create_table(
        "incidents",
        sa.Column("id", uuid_col, primary_key=True),
        sa.Column("slug", sa.String(96), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("audience", audience_col, nullable=False),
        sa.Column("severity", incident_severity_col, nullable=False),
        sa.Column("status", incident_status_col, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("linked_alert_fingerprint", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name=op.f("uq_incidents_slug")),
    )
    op.create_index("ix_incidents_status_audience", "incidents", ["status", "audience"])
    op.create_index("ix_incidents_started_at", "incidents", ["started_at"])

    op.create_table(
        "incident_components",
        sa.Column("incident_id", uuid_col, primary_key=True),
        sa.Column("component_id", sa.String(64), primary_key=True),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], ondelete="CASCADE",
            name=op.f("fk_incident_components_incident_id_incidents"),
        ),
        sa.ForeignKeyConstraint(
            ["component_id"], ["components.id"], ondelete="RESTRICT",
            name=op.f("fk_incident_components_component_id_components"),
        ),
    )

    op.create_table(
        "incident_updates",
        sa.Column("id", uuid_col, primary_key=True),
        sa.Column("incident_id", uuid_col, nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_at_post", incident_status_col, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("posted_by", sa.String(128), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], ondelete="CASCADE",
            name=op.f("fk_incident_updates_incident_id_incidents"),
        ),
    )
    op.create_index(
        "ix_incident_updates_incident_posted",
        "incident_updates",
        ["incident_id", "posted_at"],
    )

    op.create_table(
        "maintenance_windows",
        sa.Column("id", uuid_col, primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("audience", audience_col, nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_maintenance_windows_starts_at", "maintenance_windows", ["starts_at"])
    op.create_index("ix_maintenance_windows_ends_at", "maintenance_windows", ["ends_at"])

    op.create_table(
        "maintenance_components",
        sa.Column("maintenance_id", uuid_col, primary_key=True),
        sa.Column("component_id", sa.String(64), primary_key=True),
        sa.ForeignKeyConstraint(
            ["maintenance_id"], ["maintenance_windows.id"], ondelete="CASCADE",
            name=op.f("fk_maintenance_components_maintenance_id_maintenance_windows"),
        ),
        sa.ForeignKeyConstraint(
            ["component_id"], ["components.id"], ondelete="RESTRICT",
            name=op.f("fk_maintenance_components_component_id_components"),
        ),
    )

    op.create_table(
        "probe_results",
        sa.Column("id", uuid_col, primary_key=True),
        sa.Column("probe_name", sa.String(64), nullable=False),
        sa.Column("component_id", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", probe_outcome_col, nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("detail", json_col(), nullable=True),
        sa.ForeignKeyConstraint(
            ["component_id"], ["components.id"], ondelete="CASCADE",
            name=op.f("fk_probe_results_component_id_components"),
        ),
        sa.UniqueConstraint("probe_name", "observed_at", name="uq_probe_results_probe_observed"),
    )
    op.create_index("ix_probe_results_component_observed", "probe_results", ["component_id", "observed_at"])
    op.create_index("ix_probe_results_observed_at", "probe_results", ["observed_at"])


def downgrade() -> None:
    op.drop_index("ix_probe_results_observed_at", table_name="probe_results")
    op.drop_index("ix_probe_results_component_observed", table_name="probe_results")
    op.drop_table("probe_results")
    op.drop_table("maintenance_components")
    op.drop_index("ix_maintenance_windows_ends_at", table_name="maintenance_windows")
    op.drop_index("ix_maintenance_windows_starts_at", table_name="maintenance_windows")
    op.drop_table("maintenance_windows")
    op.drop_index("ix_incident_updates_incident_posted", table_name="incident_updates")
    op.drop_table("incident_updates")
    op.drop_table("incident_components")
    op.drop_index("ix_incidents_started_at", table_name="incidents")
    op.drop_index("ix_incidents_status_audience", table_name="incidents")
    op.drop_table("incidents")
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.drop_index("ix_component_overrides_active", table_name="component_overrides")
    op.drop_table("component_overrides")
    if is_postgres:
        op.drop_index("ix_component_status_history_open", table_name="component_status_history")
    op.drop_index(
        "ix_component_status_history_component_started",
        table_name="component_status_history",
    )
    op.drop_table("component_status_history")
    op.drop_table("components")
    if is_postgres:
        for enum in (
            _PROBE_OUTCOME,
            _INCIDENT_STATUS,
            _INCIDENT_SEVERITY,
            _HISTORY_SOURCE,
            _STATUS,
            _AUDIENCE,
        ):
            enum.drop(op.get_bind(), checkfirst=True)
