#!/usr/bin/env python3
"""Apply the shared incident overlays to every dashboard in this repository.

A panel should never have to be interpreted alone. Two kinds of context are
missing from a bare time series: what fired, deployed or was done by hand on
the same x-axis (time context), and the neighbouring dashboard that confirms or
refutes what a line is doing (signal context). This script stamps both onto the
committed dashboards so they stay identical everywhere and can be re-applied
after a dashboard is edited in the Grafana UI and re-exported.

Rules encoded here (see README "Incident overlays"):

* Alert annotations exclude chronic and informational alerts. Over 30 days
  HostSystemdServiceFailed accumulated 473k firing-minutes and Watchdog 150k;
  drawn as annotations they paint every timeline solid. CHRONIC_ALERTS is the
  exclusion list; extend it when a new alert turns out to be always-on rather
  than fixing the alert, never the other way round.
* Loki-ruler alerts never reach Mimir, so app-log context (restarts) comes from
  Loki log queries directly, with `line_format` producing a short marker text.
* Operator actions and deploys are Grafana-native annotations tagged
  ``ai-horde`` (posted by ops' scripts/annotate.sh and the prod deploy play), so
  one tag query shows them on every internal dashboard.
* Public/package dashboards (provisioned into the public org too) get only the
  maintenance/raid shading from the exporter alerts on their own datasource.

Usage: python3 scripts/apply_dashboard_overlays.py [--check]
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INFRA_DIR = REPO / "dashboards"
APP_DIR = REPO / "packages" / "ai-horde-stats-exporter" / "src" / "ai_horde_stats_exporter" / "dashboards"

# Alerts that are (or have been) lit for days at a time. They carry no incident
# timing information and would hide every real annotation behind a solid band.
CHRONIC_ALERTS = "|".join(
    [
        "Watchdog",
        "HostSystemdServiceFailed",
        "HostOutOfInodes",
        "HostSwapFillingUp",
        "HostDiskWillFillIn24h",
        "HostDiskUsageHigh",
        "WireGuardTextfileStale",
        "PrometheusTargetsMissing",
        "HordeRequestObservationDataReady",
        "HordeRequestEstimatorPromotionApproved",
        "HordeRequestUnstartedExpiryForecastValidationReady",
    ],
)

OVERLAY_MARKER = "overlay"  # every annotation this script owns carries {"horde_overlay": <key>}

# fmt: off
INTERNAL_ANNOTATIONS: list[dict] = [
    {
        "horde_overlay": "alerts-infra",
        "name": "Infra alerts",
        "datasource": {"type": "prometheus", "uid": "mimir-infra"},
        "enable": True, "hide": False, "iconColor": "#d03b3b",
        "expr": f'ALERTS{{alertstate="firing", severity=~"critical|warning", alertname!~"{CHRONIC_ALERTS}"}}',
        "step": "30s", "titleFormat": "{{alertname}}", "textFormat": "{{severity}} {{instance}}{{host}}{{peer}}{{proxy}}{{name}}",
        "useValueForTime": False,
    },
    {
        "horde_overlay": "alerts-telemetry",
        "name": "App alerts (telemetry)",
        "datasource": {"type": "prometheus", "uid": "mimir-telemetry"},
        "enable": True, "hide": False, "iconColor": "#ec835a",
        "expr": f'ALERTS{{alertstate="firing", severity=~"critical|warning", alertname!~"{CHRONIC_ALERTS}"}}',
        "step": "30s", "titleFormat": "{{alertname}}", "textFormat": "{{severity}} {{horde_host}}{{horde_job_name}}{{deployment_environment_name}}",
        "useValueForTime": False,
    },
    {
        "horde_overlay": "alerts-app",
        "name": "App alerts (exporter)",
        "datasource": {"type": "prometheus", "uid": "mimir-app"},
        "enable": True, "hide": False, "iconColor": "#e0b400",
        "expr": f'ALERTS{{alertstate="firing", severity=~"critical|warning", alertname!~"{CHRONIC_ALERTS}"}}',
        "step": "30s", "titleFormat": "{{alertname}}", "textFormat": "{{severity}}",
        "useValueForTime": False,
    },
    {
        "horde_overlay": "app-restarts",
        "name": "App restarts",
        "datasource": {"type": "loki", "uid": "loki-app"},
        "enable": True, "hide": False, "iconColor": "#8ab8ff",
        # LOG CONTRACT (AI-Horde horde/flask.py): "Horde Database" is the first
        # init line every backend instance logs; one marker per container start.
        "expr": '{app="ai-horde", component="backend"} |= "INIT" |= "Horde Database" | line_format "{{.instance}} {{.container}} started"',
    },
    {
        "horde_overlay": "operator",
        "name": "Operator notes & deploys",
        "datasource": {"type": "grafana", "uid": "-- Grafana --"},
        "enable": True, "hide": False, "iconColor": "#b877d9",
        "target": {"type": "tags", "tags": ["ai-horde"], "matchAny": True, "limit": 200},
    },
]

PUBLIC_ANNOTATIONS: list[dict] = [
    {
        "horde_overlay": "modes",
        "name": "Maintenance / raid mode",
        "datasource": {"type": "prometheus", "uid": "${datasource}"},
        "enable": True, "hide": False, "iconColor": "#e0b400",
        "expr": 'ALERTS{alertstate="firing", alertname=~"HordeMaintenanceMode|HordeRaidMode"}',
        "step": "30s", "titleFormat": "{{alertname}}", "textFormat": "",
        "useValueForTime": False,
    },
]
# fmt: on

# One dropdown of every dashboard tagged "horde", preserving the time range and
# variables, so an operator moves from "what" to "why" without retyping a range.
HORDE_LINK = {
    "asDropdown": True,
    "icon": "external link",
    "includeVars": True,
    "keepTime": True,
    "tags": ["horde"],
    "targetBlank": False,
    "title": "Horde Dashboards",
    "type": "dashboards",
}

# v2 (dashboard.grafana.app) exports of third-party dashboards. The classic
# conversion step turns these into the same shape as INTERNAL_ANNOTATIONS.
V2_ANNOTATION_SETS = {
    "postgres.json": ["alerts-infra", "operator", "app-restarts"],
    "node_exporter_full.json": ["alerts-infra", "operator"],
    "pm2.json": ["operator"],
}


def _merge_annotations(existing: list[dict], wanted: list[dict]) -> list[dict]:
    """Keep the builtin and any hand-written annotations, replace ours in place."""
    kept = [a for a in existing if a.get("builtIn") or "horde_overlay" not in a]
    return kept + copy.deepcopy(wanted)


def _ensure_link(links: list[dict]) -> list[dict]:
    if any(link.get("type") == "dashboards" and "horde" in link.get("tags", []) for link in links):
        return links
    return links + [copy.deepcopy(HORDE_LINK)]


def _ensure_tag(tags: list[str], tag: str) -> list[str]:
    return tags if tag in tags else tags + [tag]


def _classic(dashboard: dict, annotations: list[dict], public: bool) -> dict:
    dashboard.setdefault("annotations", {}).setdefault("list", [])
    dashboard["annotations"]["list"] = _merge_annotations(dashboard["annotations"]["list"], annotations)
    dashboard["links"] = _ensure_link(dashboard.get("links", []))
    if not public:
        dashboard["tags"] = _ensure_tag(dashboard.get("tags", []), "horde")
    return dashboard


def _v2_annotation(entry: dict) -> dict:
    """Render one of our classic annotation dicts as a v2 AnnotationQuery."""
    entry = copy.deepcopy(entry)
    spec = {
        "builtIn": False,
        "enable": entry.pop("enable"),
        "hide": entry.pop("hide"),
        "iconColor": entry.pop("iconColor"),
        "name": entry.pop("name"),
        "horde_overlay": entry.pop("horde_overlay"),
    }
    datasource = entry.pop("datasource")
    if datasource["type"] == "grafana":
        query = {"kind": "DataQuery", "group": "grafana", "version": "v0", "spec": {**entry["target"]}}
    else:
        query = {
            "kind": "DataQuery",
            "group": datasource["type"],
            "version": "v0",
            "datasource": {"name": datasource["uid"]},
            "spec": entry,
        }
    spec["query"] = query
    return {"kind": "AnnotationQuery", "spec": spec}


def _v2(resource: dict, keys: list[str]) -> dict:
    spec = resource["spec"]
    wanted = [a for a in INTERNAL_ANNOTATIONS if a["horde_overlay"] in keys]
    kept = [
        a
        for a in spec.get("annotations", [])
        if a.get("spec", {}).get("builtIn") or "horde_overlay" not in a.get("spec", {})
    ]
    spec["annotations"] = kept + [_v2_annotation(a) for a in wanted]
    spec["links"] = _ensure_link(spec.get("links", []))
    spec["tags"] = _ensure_tag(spec.get("tags", []), "horde")
    return resource


def _detect_format(original: str, data: dict) -> dict:
    """Find the json.dumps settings that reproduce the file so the rewrite only touches what changed."""
    for indent in (1, 2, 4):
        for ensure_ascii in (True, False):
            for sort_keys in (False, True):
                for trailing in ("\n", ""):
                    candidate = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii, sort_keys=sort_keys) + trailing
                    if candidate == original:
                        return {"indent": indent, "ensure_ascii": ensure_ascii, "sort_keys": sort_keys, "trailing": trailing}
    return {"indent": 2, "ensure_ascii": False, "sort_keys": False, "trailing": "\n"}


def _replace_top_level_block(original: str, key: str, value) -> str:
    """Swap the text of one top-level key in a hand-formatted (2-space) JSON file.

    The package dashboards are not json.dumps output (short objects are kept on
    one line), so re-serialising them would rewrite every line. Only the
    ``annotations`` and ``links`` blocks change, so splice those in as text.
    """
    open_token = f'\n  "{key}": '
    start = original.index(open_token)
    body_start = start + len(open_token)
    closer = "\n  }" if original[body_start] == "{" else "\n  ]"
    end = original.index(closer, body_start) + len(closer)
    rendered = json.dumps(value, indent=2, ensure_ascii=False).replace("\n", "\n  ")
    return original[:start] + open_token + rendered + original[end:]


def _dump(data: dict, fmt: dict) -> str:
    return json.dumps(data, indent=fmt["indent"], ensure_ascii=fmt["ensure_ascii"], sort_keys=fmt["sort_keys"]) + fmt["trailing"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if any dashboard would change")
    args = parser.parse_args()

    changed: list[Path] = []

    for path in sorted(INFRA_DIR.rglob("*.json")):
        original = path.read_text(encoding="utf-8")
        data = json.loads(original)
        fmt = _detect_format(original, data)
        if str(data.get("apiVersion", "")).startswith("dashboard.grafana.app/"):
            keys = V2_ANNOTATION_SETS.get(path.name)
            if keys is None:
                print(f"skip {path.relative_to(REPO)}: v2 dashboard without an overlay set", file=sys.stderr)
                continue
            data = _v2(data, keys)
        else:
            target = data.get("dashboard", data)
            _classic(target, INTERNAL_ANNOTATIONS, public=False)
        rendered = _dump(data, fmt)
        if rendered != original:
            changed.append(path)
            if not args.check:
                path.write_text(rendered, encoding="utf-8")

    for path in sorted(APP_DIR.glob("*.json")):
        original = path.read_text(encoding="utf-8")
        data = json.loads(original)
        target = data.get("dashboard", data)
        _classic(target, PUBLIC_ANNOTATIONS, public=True)
        rendered = _replace_top_level_block(original, "annotations", target["annotations"])
        rendered = _replace_top_level_block(rendered, "links", target["links"])
        if rendered != original:
            changed.append(path)
            if not args.check:
                path.write_text(rendered, encoding="utf-8")

    for path in changed:
        print(("would update " if args.check else "updated ") + str(path.relative_to(REPO)))
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
