# horde-exporters

Prometheus-compatible stats exporter for the [AI-Horde](https://aihorde.net/) distributed computing network.

## Installation

Requires **Python 3.12+**. Install with [uv](https://docs.astral.sh/uv/) (recommended) or pip:

```bash
# uv (editable / development install — installs all workspace packages)
uv sync

# pip (install individual packages)
pip install packages/ai-horde-stats-exporter
```

## Usage

```bash
# Console script (installed by pip/uv)
horde-exporter

# Or as a Python module
python -m ai_horde_stats_exporter

# Custom config path
horde-exporter --config /path/to/config.yaml

# Or via environment variable
export HORDE_CONFIG_PATH=/path/to/config.yaml
horde-exporter
```

The config file path is resolved in this order:
1. `--config` CLI argument
2. `HORDE_CONFIG_PATH` environment variable
3. `exporter_config.yaml` in the current working directory

A default `exporter_config.yaml` is included in the repository root.

All settings can also be overridden with environment variables prefixed `HORDE_` using nested delimiter `__` (e.g. `HORDE_API__BASE_URL`, `HORDE_EXPORTER__PORT`).

## Scraped Endpoints

The exporter scrapes the following AI-Horde API endpoints:

- `/status/models?type={model_type}`
- `/workers?type={worker_type}`
- `/status/performance`
- `/stats/img/totals`, `/stats/text/totals`
- `/stats/img/models`, `/stats/text/models`
- `/status/modes`, `/status/heartbeat`
- `/teams`

See the [AI-Horde API docs](https://aihorde.net/api/) for detailed information.

## Grafana Dashboards

Pre-built Grafana dashboards are bundled with the package and also available in `packages/ai-horde-stats-exporter/src/ai_horde_stats_exporter/dashboards/`. They require a Prometheus datasource pointed at this exporter (default port `9150`).

| Dashboard | File | Description |
|-----------|------|-------------|
| **Horde Performance** | `horde-performance.json` | High-level stats — API status, image/text worker counts, throughput (MPS/min, tokens/min), queue depth, historical generation stats, queue drain estimates, and teams overview. |
| **Image Workers Overview** | `horde-image-workers-overview.json` | Per-worker drill-down — summary table, performance/requests/kudos time series, image capabilities (img2img, LoRA, max pixels). |
| **Text Workers Overview** | `horde-text-workers-overview.json` | Per-worker drill-down for text workers. |
| **Image Worker Detail** | `horde-image-worker-detail.json` | Single image worker drill-down. |
| **Text Worker Detail** | `horde-text-worker-detail.json` | Single text worker drill-down. |
| **Image Models Overview** | `horde-image-models-overview.json` | All image models at a glance — aggregate stats, merged table with queue % and worker % shares, top 15 time series, domain-wide MPS throughput, and historical generation rankings. |
| **Text Models Overview** | `horde-text-models-overview.json` | All text models at a glance — aggregate stats, merged table with queue % and worker % shares, top 15 time series, domain-wide token throughput, and historical generation rankings. |
| **Image Model Detail** | `horde-image-model-detail.json` | Single image model drill-down — live stats, capacity share gauges (queue/worker/jobs), computed ratios over time, and historical generation stats with % of total. |
| **Text Model Detail** | `horde-text-model-detail.json` | Single text model drill-down — live stats, capacity share gauges (queue/worker/jobs), computed ratios over time, and historical generation stats with % of total. |

> **Note:** The legacy combined `horde-models.json` is superseded by the four model dashboards above.

### Accessing Dashboards Programmatically

```python
from ai_horde_stats_exporter.dashboards import list_dashboards, get_dashboard_path

# List all bundled dashboards
print(list_dashboards())

# Get the path to a specific dashboard
path = get_dashboard_path("horde-performance.json")
```

### Importing into Grafana

1. In Grafana, go to **Dashboards → New → Import**.
2. Upload or paste the JSON from any of the bundled dashboard files.
3. Select your Prometheus datasource when prompted.

All dashboards share a `horde` tag and cross-link via the dashboard navigation dropdown. The overview dashboards have hardcoded type filters; the detail dashboards provide a `model` template variable for drill-down.

## Project Structure

This is a **uv workspace** monorepo. Each exporter lives under `packages/`:

```
horde-exporters/
├── pyproject.toml                              # Workspace root (virtual)
├── uv.lock
├── README.md, LICENSE
└── packages/
    └── ai-horde-stats-exporter/
        ├── pyproject.toml                      # Package build config
        ├── exporter_config.yaml                # Default runtime config
        ├── tests/
        └── src/ai_horde_stats_exporter/
            ├── __init__.py       # Package metadata
            ├── __main__.py       # CLI entry point
            ├── config.py         # Settings (pydantic-settings, YAML + env vars)
            ├── models.py         # Pydantic API response models
            ├── specs.py          # Metric spec dataclasses & constant tables
            ├── metrics.py        # Prometheus metric registry
            ├── rate_limit.py     # Thread-safe rate-limit tracker
            ├── exporter.py       # Main exporter (collectors, threads, startup)
            ├── dashboards.py     # Bundled dashboard access helpers
            └── dashboards/       # Grafana dashboard JSON files
```

To add a new exporter, create a new directory under `packages/` with its own `pyproject.toml` and `src/` layout.

## Versioning Policy

Package versions are intentionally independent.

- Each sub-package under `packages/*` owns its own version.
- Sub-package versions do not need to match each other.
- Runtime/build-impacting changes in `packages/<name>/` (for example `src/**`,
    package `pyproject.toml`, build config, build helper `.py`, dependency manifests)
    must bump that sub-package's version.
- Runtime/build-impacting changes in any `packages/*` must also bump the root
    `horde-exporters` version in `pyproject.toml`.

For `ai-horde-stats-exporter`, the package version is single-sourced from
`src/ai_horde_stats_exporter/__about__.py` and re-exported from
`src/ai_horde_stats_exporter/__init__.py` via Hatch dynamic versioning.

CI enforces this policy on pull requests with
`.github/workflows/version-policy.yml`.

## Development

```bash
uv sync                                         # Install all workspace packages + dev deps
uv run ruff check packages/                     # Lint
uv run pytest                                   # Test
```
