#!/usr/bin/env python3
"""Convert Grafana v2 dashboard resource JSON into classic v1 dashboard JSON.

This converter is intentionally scoped to the v2 resources currently committed
under dashboards/ in this repository. It preserves panel queries, layout,
variables, time settings, annotations, links, and basic dashboard metadata.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


HIDE_MAP = {
    "dontHide": 0,
    "hideLabel": 1,
    "hideVariable": 2,
}

REFRESH_MAP = {
    "never": 0,
    "onDashboardLoad": 1,
    "onTimeRangeChanged": 2,
}

SORT_MAP = {
    "disabled": 0,
    "alphabeticalAsc": 1,
    "alphabeticalDesc": 2,
    "numericalAsc": 3,
    "numericalDesc": 4,
    "alphabeticalCaseInsensitiveAsc": 5,
    "alphabeticalCaseInsensitiveDesc": 6,
}

CURSOR_SYNC_TO_GRAPH_TOOLTIP = {
    "Off": 0,
    "Crosshair": 1,
    "Tooltip": 2,
}


def _map_hide(value: Any) -> int:
    return HIDE_MAP.get(value, 0)


def _map_refresh(value: Any) -> int:
    return REFRESH_MAP.get(value, 0)


def _map_sort(value: Any) -> int:
    return SORT_MAP.get(value, 0)


def _to_datasource_ref(ds: Any, query_group: str | None = None) -> dict[str, Any] | None:
    if not isinstance(ds, dict):
        return None

    result: dict[str, Any] = {}
    if isinstance(ds.get("type"), str):
        result["type"] = ds["type"]
    elif isinstance(query_group, str):
        result["type"] = query_group

    if isinstance(ds.get("uid"), str):
        result["uid"] = ds["uid"]
    elif isinstance(ds.get("name"), str):
        # Classic dashboards model datasource references via `uid`; in legacy
        # shared dashboards this can still be a ${DS_*} placeholder.
        result["uid"] = ds["name"]

    if not result:
        return None
    return result


def _convert_query(panel_query: dict[str, Any]) -> dict[str, Any]:
    qspec = panel_query.get("spec", {}) if isinstance(panel_query.get("spec"), dict) else {}
    dq = qspec.get("query", {}) if isinstance(qspec.get("query"), dict) else {}
    dq_spec = copy.deepcopy(dq.get("spec", {})) if isinstance(dq.get("spec"), dict) else {}

    target = dq_spec
    ds_ref = _to_datasource_ref(dq.get("datasource"), dq.get("group"))
    if ds_ref is None and isinstance(dq.get("group"), str):
        ds_ref = {"type": dq["group"]}
    if ds_ref:
        target["datasource"] = ds_ref
    if isinstance(qspec.get("refId"), str):
        target["refId"] = qspec["refId"]
    if qspec.get("hidden") is True:
        target["hide"] = True
    return target


def _convert_panel(panel_spec: dict[str, Any]) -> dict[str, Any]:
    panel: dict[str, Any] = {}

    for key, value in panel_spec.items():
        if key in ("data", "vizConfig"):
            continue
        panel[key] = copy.deepcopy(value)

    viz = panel_spec.get("vizConfig")
    if isinstance(viz, dict):
        if isinstance(viz.get("group"), str):
            panel["type"] = viz["group"]
        if isinstance(viz.get("version"), str):
            panel["pluginVersion"] = viz["version"]
        viz_spec = viz.get("spec")
        if isinstance(viz_spec, dict):
            if "fieldConfig" in viz_spec:
                panel["fieldConfig"] = copy.deepcopy(viz_spec["fieldConfig"])
            if "options" in viz_spec:
                panel["options"] = copy.deepcopy(viz_spec["options"])

    data = panel_spec.get("data")
    if isinstance(data, dict):
        data_spec = data.get("spec") if isinstance(data.get("spec"), dict) else {}
        queries = data_spec.get("queries") if isinstance(data_spec.get("queries"), list) else []
        panel["targets"] = [_convert_query(q) for q in queries if isinstance(q, dict)]

        query_options = data_spec.get("queryOptions")
        if isinstance(query_options, dict):
            for key in ("maxDataPoints", "interval", "intervalMs", "minInterval"):
                if key in query_options:
                    panel[key] = copy.deepcopy(query_options[key])

        if isinstance(data_spec.get("transformations"), list) and data_spec["transformations"]:
            panel["transformations"] = copy.deepcopy(data_spec["transformations"])

    return panel


def _convert_annotations(v2_annotations: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ann in v2_annotations:
        if not isinstance(ann, dict):
            continue
        if ann.get("kind") != "AnnotationQuery":
            continue

        spec = ann.get("spec") if isinstance(ann.get("spec"), dict) else {}
        entry: dict[str, Any] = {}
        for key in ("enable", "hide", "iconColor", "name"):
            if key in spec:
                entry[key] = copy.deepcopy(spec[key])

        if spec.get("builtIn"):
            entry["builtIn"] = 1

        query = spec.get("query") if isinstance(spec.get("query"), dict) else {}
        if query.get("group") == "grafana":
            entry["type"] = "dashboard"
            qspec = query.get("spec") if isinstance(query.get("spec"), dict) else {}
            if qspec.get("type") == "dashboard":
                entry.setdefault("type", "dashboard")
                if any(k in qspec for k in ("limit", "matchAny", "tags")):
                    entry["target"] = {
                        "limit": qspec.get("limit", 100),
                        "matchAny": qspec.get("matchAny", False),
                        "tags": qspec.get("tags", []),
                        "type": "dashboard",
                    }

        out.append(entry)
    return out


def _convert_variable(var: dict[str, Any]) -> dict[str, Any] | None:
    kind = var.get("kind")
    spec = var.get("spec") if isinstance(var.get("spec"), dict) else {}
    if not isinstance(spec, dict):
        return None

    if kind == "DatasourceVariable":
        out: dict[str, Any] = {
            "type": "datasource",
            "name": spec.get("name", ""),
            "query": spec.get("pluginId", ""),
            "hide": _map_hide(spec.get("hide")),
            "includeAll": bool(spec.get("includeAll", False)),
            "multi": bool(spec.get("multi", False)),
            "refresh": _map_refresh(spec.get("refresh")),
            "regex": spec.get("regex", ""),
            "options": copy.deepcopy(spec.get("options", [])),
            "current": copy.deepcopy(spec.get("current", {"text": "", "value": ""})),
        }
        if isinstance(spec.get("label"), str) and spec["label"]:
            out["label"] = spec["label"]
        if spec.get("allowCustomValue") is True:
            out["allowCustomValue"] = True
        return out

    if kind == "IntervalVariable":
        out = {
            "type": "interval",
            "name": spec.get("name", ""),
            "query": spec.get("query", ""),
            "hide": _map_hide(spec.get("hide")),
            "refresh": _map_refresh(spec.get("refresh")),
            "options": copy.deepcopy(spec.get("options", [])),
            "current": copy.deepcopy(spec.get("current", {"text": "", "value": ""})),
        }
        for key in ("auto", "auto_count", "auto_min"):
            if key in spec:
                out[key] = copy.deepcopy(spec[key])
        if isinstance(spec.get("label"), str) and spec["label"]:
            out["label"] = spec["label"]
        return out

    if kind == "QueryVariable":
        query = spec.get("query") if isinstance(spec.get("query"), dict) else {}
        query_spec = query.get("spec") if isinstance(query.get("spec"), dict) else {}

        query_text = ""
        if isinstance(query_spec.get("__legacyStringValue"), str):
            query_text = query_spec["__legacyStringValue"]
        elif isinstance(query_spec.get("query"), str):
            query_text = query_spec["query"]
        elif isinstance(spec.get("definition"), str):
            query_text = spec.get("definition", "")

        out = {
            "type": "query",
            "name": spec.get("name", ""),
            "query": query_text,
            "hide": _map_hide(spec.get("hide")),
            "includeAll": bool(spec.get("includeAll", False)),
            "multi": bool(spec.get("multi", False)),
            "refresh": _map_refresh(spec.get("refresh")),
            "regex": spec.get("regex", ""),
            "options": copy.deepcopy(spec.get("options", [])),
            "current": copy.deepcopy(spec.get("current", {"text": "", "value": ""})),
            "sort": _map_sort(spec.get("sort")),
        }

        if isinstance(spec.get("label"), str) and spec["label"]:
            out["label"] = spec["label"]
        if isinstance(spec.get("definition"), str):
            out["definition"] = spec["definition"]
        if isinstance(spec.get("regexApplyTo"), str):
            out["regexApplyTo"] = spec["regexApplyTo"]
        if spec.get("allowCustomValue") is True:
            out["allowCustomValue"] = True

        ds_ref = _to_datasource_ref(query.get("datasource"), query.get("group"))
        if ds_ref is None and isinstance(query.get("group"), str):
            ds_ref = {"type": query["group"]}
        if ds_ref:
            out["datasource"] = ds_ref

        return out

    return None


def _layout_panels(spec: dict[str, Any], panel_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    layout = spec.get("layout") if isinstance(spec.get("layout"), dict) else {}
    layout_kind = layout.get("kind")
    layout_spec = layout.get("spec") if isinstance(layout.get("spec"), dict) else {}

    max_panel_id = max((p.get("id", 0) for p in panel_map.values() if isinstance(p.get("id"), int)), default=0)
    next_row_id = max_panel_id + 1

    def build_grid_panels(items: list[Any], y_offset: int) -> tuple[list[dict[str, Any]], list[int]]:
        built: list[dict[str, Any]] = []
        bottoms: list[int] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("kind") != "GridLayoutItem":
                continue
            ispec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
            element = ispec.get("element") if isinstance(ispec.get("element"), dict) else {}
            element_name = element.get("name")
            if not isinstance(element_name, str):
                continue
            panel = panel_map.get(element_name)
            if not panel:
                continue

            h = int(ispec.get("height", 0))
            w = int(ispec.get("width", 0))
            x = int(ispec.get("x", 0))
            y = int(ispec.get("y", 0)) + y_offset
            panel_obj = copy.deepcopy(panel)
            panel_obj["gridPos"] = {"h": h, "w": w, "x": x, "y": y}
            built.append(panel_obj)
            bottoms.append(y + h)
        return built, bottoms

    if layout_kind == "GridLayout":
        items = layout_spec.get("items") if isinstance(layout_spec.get("items"), list) else []
        grid_panels, _ = build_grid_panels(items, 0)
        out.extend(grid_panels)
        return out

    if layout_kind == "RowsLayout":
        rows = layout_spec.get("rows") if isinstance(layout_spec.get("rows"), list) else []
        global_y = 0
        for row in rows:
            if not isinstance(row, dict) or row.get("kind") != "RowsLayoutRow":
                continue
            rspec = row.get("spec") if isinstance(row.get("spec"), dict) else {}
            row_panel = {
                "collapsed": bool(rspec.get("collapse", False)),
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": global_y},
                "id": next_row_id,
                "panels": [],
                "title": rspec.get("title", ""),
                "type": "row",
            }
            next_row_id += 1
            out.append(row_panel)

            row_layout = rspec.get("layout") if isinstance(rspec.get("layout"), dict) else {}
            row_layout_spec = row_layout.get("spec") if isinstance(row_layout.get("spec"), dict) else {}
            row_items = row_layout_spec.get("items") if isinstance(row_layout_spec.get("items"), list) else []

            row_panels, row_bottoms = build_grid_panels(row_items, global_y + 1)
            row_height = (max(row_bottoms) - (global_y + 1)) if row_bottoms else 0

            if row_panel["collapsed"]:
                row_panel["panels"] = row_panels
                # Collapsed rows consume only the header row in top-level layout.
                global_y += 1
            else:
                out.extend(row_panels)
                global_y += 1 + max(0, row_height)

        return out

    raise ValueError(f"Unsupported layout kind for conversion: {layout_kind}")


def convert_dashboard(v2: dict[str, Any]) -> dict[str, Any]:
    api_version = v2.get("apiVersion")
    spec = v2.get("spec") if isinstance(v2.get("spec"), dict) else None

    if not (isinstance(api_version, str) and api_version.startswith("dashboard.grafana.app/") and isinstance(spec, dict)):
        return v2

    elements = spec.get("elements") if isinstance(spec.get("elements"), dict) else {}
    panel_map: dict[str, dict[str, Any]] = {}
    for key, value in elements.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        if value.get("kind") != "Panel":
            continue
        pspec = value.get("spec") if isinstance(value.get("spec"), dict) else {}
        panel_map[key] = _convert_panel(pspec)

    v1: dict[str, Any] = {
        "annotations": {
            "list": _convert_annotations(spec.get("annotations", []) if isinstance(spec.get("annotations"), list) else [])
        },
        "editable": bool(spec.get("editable", True)),
        "links": copy.deepcopy(spec.get("links", [])) if isinstance(spec.get("links"), list) else [],
        "panels": _layout_panels(spec, panel_map),
        "preload": bool(spec.get("preload", False)),
        "tags": copy.deepcopy(spec.get("tags", [])) if isinstance(spec.get("tags"), list) else [],
        "timepicker": {},
        "title": spec.get("title", ""),
        "version": 0,
        "weekStart": "",
    }

    metadata = v2.get("metadata") if isinstance(v2.get("metadata"), dict) else {}
    if isinstance(metadata.get("name"), str):
        v1["uid"] = metadata["name"]
    if isinstance(spec.get("description"), str):
        v1["description"] = spec["description"]

    time_settings = spec.get("timeSettings") if isinstance(spec.get("timeSettings"), dict) else {}
    v1["fiscalYearStartMonth"] = int(time_settings.get("fiscalYearStartMonth", 0))
    v1["refresh"] = time_settings.get("autoRefresh", "") or ""
    v1["time"] = {
        "from": time_settings.get("from", "now-6h"),
        "to": time_settings.get("to", "now"),
    }
    v1["timezone"] = time_settings.get("timezone", "browser")

    intervals = time_settings.get("autoRefreshIntervals")
    if isinstance(intervals, list):
        v1["timepicker"]["refresh_intervals"] = copy.deepcopy(intervals)

    cursor_sync = spec.get("cursorSync")
    v1["graphTooltip"] = CURSOR_SYNC_TO_GRAPH_TOOLTIP.get(cursor_sync, 0)

    variables = spec.get("variables") if isinstance(spec.get("variables"), list) else []
    templating_list = []
    for var in variables:
        if not isinstance(var, dict):
            continue
        converted = _convert_variable(var)
        if converted is not None:
            templating_list.append(converted)
    v1["templating"] = {"list": templating_list}

    return v1


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Grafana v2 dashboard JSON to classic v1 JSON")
    parser.add_argument("input", help="Path to the source dashboard JSON")
    parser.add_argument("output", nargs="?", help="Output path (defaults to in-place)")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output) if args.output else in_path

    data = json.loads(in_path.read_text())
    converted = convert_dashboard(data)
    out_path.write_text(json.dumps(converted, indent=2) + "\n")


if __name__ == "__main__":
    main()
