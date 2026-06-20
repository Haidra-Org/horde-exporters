"""Curated alertname → public component mapping.

Only alerts whose ``alertname`` (and, optionally, additional label values)
match an entry in :file:`config/alert_component_map.yaml` may flip a public
component pill. Anything outside this allowlist is internal-only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ai_horde_service_alerts.db.types import ComponentStatusValue
from ai_horde_service_alerts.models.internal import AlertmanagerAlert

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AlertMatch:
    """One row from the curated map."""

    alertname: str
    component_id: str
    status: ComponentStatusValue
    label_match: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AlertMappingResult:
    """The component+status that an alert resolves to (if any)."""

    component_id: str
    status: ComponentStatusValue
    alertname: str


class _AlertMappingRuleConfig(BaseModel):
    """Represents one validated row from ``alert_component_map.yaml``."""

    model_config = ConfigDict(extra="forbid")

    component: str = Field(min_length=1)
    alertname: str = Field(min_length=1)
    status: ComponentStatusValue
    label_match: dict[str, str] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def _validate_status(cls, status: ComponentStatusValue) -> ComponentStatusValue:
        if status in (ComponentStatusValue.OPERATIONAL, ComponentStatusValue.UNKNOWN):
            raise ValueError("status values operational/unknown are reserved for derived state")
        return status


class _AlertMappingFileConfig(BaseModel):
    """Represents the validated top-level YAML mapping file."""

    model_config = ConfigDict(extra="forbid")

    mappings: list[_AlertMappingRuleConfig] = Field(default_factory=list)


class AlertMapping:
    """Loaded curated map. Use :meth:`resolve` to classify an alert."""

    def __init__(self, matches: list[AlertMatch]) -> None:
        """Index ``matches`` by alertname for O(1) lookups."""
        self._by_alertname: dict[str, list[AlertMatch]] = {}
        for match in matches:
            self._by_alertname.setdefault(match.alertname, []).append(match)

    @classmethod
    def from_yaml(cls, path: Path) -> AlertMapping:
        """Load the mapping from a YAML file."""
        if not path.exists():
            logger.warning("alert mapping yaml not found at %s; loading empty mapping", path)
            return cls([])
        with path.open("r", encoding="utf-8") as fp:
            loaded = yaml.safe_load(fp)  # type: ignore[no-untyped-call]
        parsed: _AlertMappingFileConfig
        try:
            parsed = _AlertMappingFileConfig.model_validate(loaded or {})
        except ValidationError as exc:
            raise ValueError(f"invalid alert mapping yaml at {path}: {exc}") from exc
        matches: list[AlertMatch] = []
        for row in parsed.mappings:
            normalized = tuple(sorted(row.label_match.items()))
            matches.append(
                AlertMatch(
                    alertname=row.alertname,
                    component_id=row.component,
                    status=row.status,
                    label_match=normalized,
                ),
            )
        logger.info("loaded %d curated alert→component rules from %s", len(matches), path)
        return cls(matches)

    def resolve(self, alert: AlertmanagerAlert) -> list[AlertMappingResult]:
        """Return every component impact for ``alert`` (worst-of-many handled by caller)."""
        alertname = alert.labels.get("alertname")
        if not alertname:
            return []
        candidates = self._by_alertname.get(alertname)
        if not candidates:
            return []
        results: list[AlertMappingResult] = []
        for candidate in candidates:
            if not _labels_match(alert.labels, candidate.label_match):
                continue
            results.append(
                AlertMappingResult(
                    component_id=candidate.component_id,
                    status=candidate.status,
                    alertname=alertname,
                ),
            )
        return results

    def known_components(self) -> set[str]:
        """Return the set of component ids referenced by any rule."""
        out: set[str] = set()
        for matches in self._by_alertname.values():
            for match in matches:
                out.add(match.component_id)
        return out

    def known_alertnames(self) -> set[str]:
        """Return every alertname that maps to at least one component."""
        return set(self._by_alertname)


def _labels_match(labels: dict[str, str], required: Iterable[tuple[str, str]]) -> bool:
    return all(labels.get(key) == value for key, value in required)
