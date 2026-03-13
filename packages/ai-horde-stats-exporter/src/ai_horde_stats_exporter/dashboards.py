"""Helpers for accessing bundled Grafana dashboard JSON files."""

from importlib.resources import files
from pathlib import Path


_DASHBOARDS = files("ai_horde_stats_exporter") / "dashboards"


def list_dashboards() -> list[str]:
    """Return the filenames of all bundled dashboard JSON files."""
    return sorted(
        item.name
        for item in _DASHBOARDS.iterdir()
        if item.name.endswith(".json")
    )


def get_dashboard_path(name: str) -> Path:
    """Return the filesystem path to a bundled dashboard JSON file.

    Raises ``FileNotFoundError`` if *name* is not a known dashboard.
    """
    resource = _DASHBOARDS / name
    path = Path(str(resource))
    if not path.exists():
        raise FileNotFoundError(f"Dashboard not found: {name}")
    return path
