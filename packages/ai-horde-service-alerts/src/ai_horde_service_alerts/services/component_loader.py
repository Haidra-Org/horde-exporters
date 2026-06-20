"""Load the component registry from YAML and seed the DB on startup."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_horde_service_alerts.db.repositories import ComponentRepository
from ai_horde_service_alerts.db.repositories.components import ComponentUpsertRow
from ai_horde_service_alerts.db.session import DatabaseBundle
from ai_horde_service_alerts.db.types import Audience

logger = logging.getLogger(__name__)


class _ComponentEntryConfig(BaseModel):
    """Represents one validated component row from the YAML registry."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str | None = None
    description: str | None = None
    audience: Audience = Audience.INTERNAL
    display_order: int = 0
    enabled: bool = True


class _ComponentsFileConfig(BaseModel):
    """Represents the validated top-level components registry file."""

    model_config = ConfigDict(extra="forbid")

    components: list[_ComponentEntryConfig] = Field(default_factory=list)


def _load_yaml(path: Path) -> _ComponentsFileConfig:
    if not path.exists():
        raise FileNotFoundError(f"components config not found: {path}")
    with path.open("r", encoding="utf-8") as fp:
        loaded = yaml.safe_load(fp)  # type: ignore[no-untyped-call]
    try:
        return _ComponentsFileConfig.model_validate(loaded or {})
    except ValidationError as exc:
        raise ValueError(f"invalid components config at {path}: {exc}") from exc


def _normalize(config: _ComponentsFileConfig) -> list[ComponentUpsertRow]:
    normalized: list[ComponentUpsertRow] = []
    for row in config.components:
        component_id = row.id
        normalized.append(
            {
                "id": component_id,
                "name": row.name or component_id,
                "description": row.description or "",
                "audience": row.audience,
                "display_order": row.display_order,
                "enabled": row.enabled,
            },
        )
    return normalized


async def seed_components(database: DatabaseBundle, *, components_path: Path) -> int:
    """Read the YAML file and upsert every component into the DB.

    Returns the number of rows applied.
    """
    rows = _normalize(_load_yaml(components_path))
    async with database.session() as session:
        repo = ComponentRepository(session)
        await repo.upsert_many(rows)
    logger.info("seeded %d component rows from %s", len(rows), components_path)
    return len(rows)
