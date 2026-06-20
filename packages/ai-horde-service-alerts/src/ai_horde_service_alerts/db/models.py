"""SQLAlchemy ORM models for the status-page database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_horde_service_alerts.db.base import Base
from ai_horde_service_alerts.db.types import (
    Audience,
    ComponentStatusValue,
    HistorySource,
    HistoryTrigger,
    IncidentSeverity,
    IncidentStatus,
    ProbeOutcome,
)

# Cross-dialect column types: native JSONB / UUID on Postgres, portable
# JSON / 16-byte UUID on SQLite (used in tests).
_JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
_UUID_TYPE = Uuid()


class TZDateTime(TypeDecorator[datetime]):
    """A timezone-aware UTC datetime that round-trips on SQLite too.

    Postgres ``timestamptz`` returns aware datetimes, but SQLite (used in
    tests) drops the tzinfo and returns naive values — which then blow up any
    arithmetic against the aware ``datetime.now(tz=UTC)`` the app uses. This
    decorator normalizes to aware UTC on the way in and out, so application
    code always sees aware datetimes regardless of backend.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Store as aware UTC (the DB may strip the tz; reads re-attach it)."""
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Re-attach UTC to values that came back naive (SQLite)."""
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


# Use everywhere a timestamp column is declared (replaces DateTime(timezone=True)).
_DATETIME_TYPE = TZDateTime()

_AUDIENCE_ENUM = Enum(
    Audience,
    name="audience",
    values_callable=lambda enum: [member.value for member in enum],
)
_STATUS_ENUM = Enum(
    ComponentStatusValue,
    name="component_status",
    values_callable=lambda enum: [member.value for member in enum],
)
_HISTORY_SOURCE_ENUM = Enum(
    HistorySource,
    name="history_source",
    values_callable=lambda enum: [member.value for member in enum],
)
_INCIDENT_SEVERITY_ENUM = Enum(
    IncidentSeverity,
    name="incident_severity",
    values_callable=lambda enum: [member.value for member in enum],
)
_INCIDENT_STATUS_ENUM = Enum(
    IncidentStatus,
    name="incident_status",
    values_callable=lambda enum: [member.value for member in enum],
)
_PROBE_OUTCOME_ENUM = Enum(
    ProbeOutcome,
    name="probe_outcome",
    values_callable=lambda enum: [member.value for member in enum],
)


def _uuid7() -> uuid.UUID:
    """Generate a UUID for surrogate primary keys."""
    return uuid.uuid4()


class Component(Base):
    """A service or infrastructure piece displayed on the status page."""

    __tablename__ = "components"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    audience: Mapped[Audience] = mapped_column(_AUDIENCE_ENUM, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        _DATETIME_TYPE,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        _DATETIME_TYPE,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    history: Mapped[list[ComponentStatusHistory]] = relationship(
        back_populates="component",
        cascade="all, delete-orphan",
    )
    overrides: Mapped[list[ComponentOverride]] = relationship(
        back_populates="component",
        cascade="all, delete-orphan",
    )


class ComponentStatusHistory(Base):
    """Closed-open intervals describing each state slice for a component."""

    __tablename__ = "component_status_history"

    id: Mapped[uuid.UUID] = mapped_column(_UUID_TYPE, primary_key=True, default=_uuid7)
    component_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("components.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[ComponentStatusValue] = mapped_column(_STATUS_ENUM, nullable=False)
    source: Mapped[HistorySource] = mapped_column(_HISTORY_SOURCE_ENUM, nullable=False)
    started_at: Mapped[datetime] = mapped_column(_DATETIME_TYPE, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(_DATETIME_TYPE, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[HistoryTrigger | None] = mapped_column(_JSON_TYPE, nullable=True)

    component: Mapped[Component] = relationship(back_populates="history")

    __table_args__ = (
        Index("ix_component_status_history_component_started", "component_id", "started_at"),
        Index(
            "ix_component_status_history_open",
            "component_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
            sqlite_where=text("ended_at IS NULL"),
        ),
    )


class ComponentOverride(Base):
    """Operator-pinned status that supersedes derived sources."""

    __tablename__ = "component_overrides"

    id: Mapped[uuid.UUID] = mapped_column(_UUID_TYPE, primary_key=True, default=_uuid7)
    component_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("components.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_status: Mapped[ComponentStatusValue] = mapped_column(_STATUS_ENUM, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        _DATETIME_TYPE,
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime | None] = mapped_column(_DATETIME_TYPE, nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(_DATETIME_TYPE, nullable=True)
    cleared_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    component: Mapped[Component] = relationship(back_populates="overrides")

    __table_args__ = (
        Index(
            "ix_component_overrides_active",
            "component_id",
            unique=True,
            postgresql_where=text("cleared_at IS NULL"),
            sqlite_where=text("cleared_at IS NULL"),
        ),
    )


class Incident(Base):
    """Operator-authored incident, the only thing that produces public prose."""

    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(_UUID_TYPE, primary_key=True, default=_uuid7)
    slug: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    audience: Mapped[Audience] = mapped_column(_AUDIENCE_ENUM, nullable=False)
    severity: Mapped[IncidentSeverity] = mapped_column(_INCIDENT_SEVERITY_ENUM, nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(_INCIDENT_STATUS_ENUM, nullable=False)
    started_at: Mapped[datetime] = mapped_column(_DATETIME_TYPE, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(_DATETIME_TYPE, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    linked_alert_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _DATETIME_TYPE,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        _DATETIME_TYPE,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    components: Mapped[list[Component]] = relationship(
        secondary="incident_components",
        lazy="selectin",
    )
    updates: Mapped[list[IncidentUpdate]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="IncidentUpdate.posted_at.desc()",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_incidents_status_audience", "status", "audience"),
        Index("ix_incidents_started_at", "started_at"),
    )


class IncidentComponent(Base):
    """Many-to-many: which components an incident says it 'Affects'."""

    __tablename__ = "incident_components"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        _UUID_TYPE,
        ForeignKey("incidents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    component_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("components.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class IncidentUpdate(Base):
    """A single timeline entry on an incident."""

    __tablename__ = "incident_updates"

    id: Mapped[uuid.UUID] = mapped_column(_UUID_TYPE, primary_key=True, default=_uuid7)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        _UUID_TYPE,
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
    )
    posted_at: Mapped[datetime] = mapped_column(_DATETIME_TYPE, nullable=False)
    status_at_post: Mapped[IncidentStatus] = mapped_column(_INCIDENT_STATUS_ENUM, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    posted_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    incident: Mapped[Incident] = relationship(back_populates="updates")

    __table_args__ = (Index("ix_incident_updates_incident_posted", "incident_id", "posted_at"),)


class MaintenanceWindow(Base):
    """Operator-scheduled maintenance window."""

    __tablename__ = "maintenance_windows"

    id: Mapped[uuid.UUID] = mapped_column(_UUID_TYPE, primary_key=True, default=_uuid7)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    audience: Mapped[Audience] = mapped_column(_AUDIENCE_ENUM, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(_DATETIME_TYPE, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(_DATETIME_TYPE, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    cancelled_at: Mapped[datetime | None] = mapped_column(_DATETIME_TYPE, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(_DATETIME_TYPE, nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(_DATETIME_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _DATETIME_TYPE,
        nullable=False,
        server_default=func.now(),
    )

    components: Mapped[list[Component]] = relationship(
        secondary="maintenance_components",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_maintenance_windows_starts_at", "starts_at"),
        Index("ix_maintenance_windows_ends_at", "ends_at"),
    )


class MaintenanceComponent(Base):
    """Many-to-many between maintenance windows and components."""

    __tablename__ = "maintenance_components"

    maintenance_id: Mapped[uuid.UUID] = mapped_column(
        _UUID_TYPE,
        ForeignKey("maintenance_windows.id", ondelete="CASCADE"),
        primary_key=True,
    )
    component_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("components.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class ProbeResult(Base):
    """A single blackbox-prober sample. Used by the evaluator and pruned periodically."""

    __tablename__ = "probe_results"

    id: Mapped[uuid.UUID] = mapped_column(_UUID_TYPE, primary_key=True, default=_uuid7)
    probe_name: Mapped[str] = mapped_column(String(64), nullable=False)
    component_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("components.id", ondelete="CASCADE"),
        nullable=False,
    )
    observed_at: Mapped[datetime] = mapped_column(_DATETIME_TYPE, nullable=False)
    outcome: Mapped[ProbeOutcome] = mapped_column(_PROBE_OUTCOME_ENUM, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(_JSON_TYPE, nullable=True)

    __table_args__ = (
        Index("ix_probe_results_component_observed", "component_id", "observed_at"),
        Index("ix_probe_results_observed_at", "observed_at"),
        UniqueConstraint("probe_name", "observed_at", name="uq_probe_results_probe_observed"),
    )
