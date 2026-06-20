"""Per-aggregate repository modules. Keep all SQL in here."""

from ai_horde_service_alerts.db.repositories.components import ComponentRepository
from ai_horde_service_alerts.db.repositories.history import HistoryRepository
from ai_horde_service_alerts.db.repositories.incidents import IncidentRepository
from ai_horde_service_alerts.db.repositories.maintenance import MaintenanceRepository
from ai_horde_service_alerts.db.repositories.overrides import OverrideRepository
from ai_horde_service_alerts.db.repositories.probe_results import ProbeResultRepository

__all__ = [
    "ComponentRepository",
    "HistoryRepository",
    "IncidentRepository",
    "MaintenanceRepository",
    "OverrideRepository",
    "ProbeResultRepository",
]
