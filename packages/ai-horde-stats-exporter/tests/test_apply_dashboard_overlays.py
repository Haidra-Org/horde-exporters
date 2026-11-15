"""The overlay script's --internal mode stamps dashboards given on the command line.

Deployments run the copy shipped in the release tarball (tools/apply_dashboard_overlays.py)
against dashboards kept outside this repository, so the tests run a copy of the script
outside the repository as well.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "apply_dashboard_overlays.py"

BUILTIN = {
    "builtIn": 1,
    "name": "Annotations & Alerts",
    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
}
HAND_WRITTEN = {
    "name": "Local marker",
    "datasource": {"type": "loki", "uid": "loki-app"},
    "expr": '{app="x"}',
}


@pytest.fixture
def tool(tmp_path: Path) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    return Path(shutil.copy(SCRIPT, tools / SCRIPT.name))


def run(tool: Path, *args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(tool), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


def write_classic(path: Path) -> Path:
    dashboard = {
        "uid": "extra-example",
        "title": "Extra example",
        "tags": ["tools"],
        "annotations": {"list": [BUILTIN, HAND_WRITTEN]},
        "panels": [],
        "templating": {"list": []},
    }
    path.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
    return path


def test_stamps_internal_overlays_and_link(tool: Path, tmp_path: Path) -> None:
    dashboard = write_classic(tmp_path / "extra.json")

    result = run(tool, "--internal", dashboard)

    assert result.returncode == 0, result.stderr
    assert "updated" in result.stdout
    data = json.loads(dashboard.read_text(encoding="utf-8"))
    annotations = data["annotations"]["list"]
    assert annotations[:2] == [BUILTIN, HAND_WRITTEN]
    assert [a["horde_overlay"] for a in annotations[2:]] == [
        "alerts-infra",
        "alerts-telemetry",
        "alerts-app",
        "app-restarts",
        "operator",
    ]
    assert [link["title"] for link in data["links"]] == ["Horde Dashboards"]
    assert data["tags"] == ["tools", "horde"]


def test_second_run_does_not_change_the_file(tool: Path, tmp_path: Path) -> None:
    dashboard = write_classic(tmp_path / "extra.json")
    run(tool, "--internal", dashboard)
    stamped = dashboard.read_text(encoding="utf-8")

    result = run(tool, "--internal", dashboard)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert dashboard.read_text(encoding="utf-8") == stamped


def test_check_reports_without_writing(tool: Path, tmp_path: Path) -> None:
    dashboard = write_classic(tmp_path / "extra.json")
    original = dashboard.read_text(encoding="utf-8")

    pending = run(tool, "--check", "--internal", dashboard)

    assert pending.returncode == 1
    assert "would update" in pending.stdout
    assert dashboard.read_text(encoding="utf-8") == original

    run(tool, "--internal", dashboard)
    assert run(tool, "--internal", dashboard, "--check").returncode == 0


def test_v2_dashboard_without_overlay_set_is_skipped(
    tool: Path, tmp_path: Path
) -> None:
    dashboard = tmp_path / "custom-v2.json"
    original = (
        json.dumps(
            {
                "apiVersion": "dashboard.grafana.app/v2beta1",
                "spec": {"annotations": []},
            },
            indent=2,
        )
        + "\n"
    )
    dashboard.write_text(original, encoding="utf-8")

    result = run(tool, "--internal", dashboard)

    assert result.returncode == 0
    assert "skip" in result.stderr
    assert dashboard.read_text(encoding="utf-8") == original
