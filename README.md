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

Infra dashboards shipped in release artifacts are emitted as classic dashboard JSON (v1 model) to remain compatible with Grafana file provisioning flows.

### Incident overlays

A panel should never have to be interpreted alone. `scripts/apply_dashboard_overlays.py`
stamps one shared annotation set and a preserved-time-range "Horde Dashboards" link onto every
dashboard in the repo (run it after editing any dashboard; `--check` is the CI guard):

| Overlay | Source | Shows |
|---|---|---|
| Infra alerts | `mimir-infra` `ALERTS` | critical/warning alerts from the infrastructure tenant, chronic ones excluded (`CHRONIC_ALERTS` in the script) |
| App alerts (telemetry) | `mimir-telemetry` `ALERTS` | OTLP-derived app alerts (latency, kudos, background jobs) |
| App alerts (exporter) | `mimir-app` `ALERTS` | exporter-derived alerts (API down, queue backlog, modes) |
| App restarts | `loki-app` | one marker per backend container start (the `INIT | Started | Horde Database` line; a LOG CONTRACT in AI-Horde) |
| Operator notes & deploys | Grafana annotations tagged `ai-horde` | `ops/scripts/annotate.sh` notes and the prod deploy play's rollout region |

Public/package dashboards (provisioned into the public org too) only get maintenance/raid
shading from their own datasource. The third-party v2 exports (Postgres, Node Exporter Full,
pm2) receive the infra-alert and operator overlays at conversion time, which
`scripts/convert_grafana_v2_to_classic.py` now carries through.

| Dashboard | File | Description |
|-----------|------|-------------|
| **Horde Performance** | `horde-performance.json` | Operator Pulse top row (active alerts + key worker/queue/scrape stats), demoted Operator Modes row, per-period stat strips for historical generation stats (replaces the legacy bargauges), Pop Diagnostics, Generation Outcomes, Teams, and Exporter Health. |
| **Horde Performance (public variant)** | `horde-performance-public.json` | Same as above without the Alertmanager `alertlist` panel and OTLP-only rows. Provisioned to the public Grafana org by the deployments role. |
| **Horde Operations Overview** | `horde-operations-overview.json` | Single-pane operator dashboard: active alerts (Alertmanager) + p95 latencies (mimir-telemetry) + worker drop indicators + Postgres saturation + DB pool timeouts + host top-N + Loki critical/error log volume. **Org 1 (admin) only — relies on Alertmanager / mimir-telemetry / loki-app datasources.** |
| **Horde Request Scheduling Validation** | `dashboards/ai_horde_otlp/horde-request-scheduling.json` | Production request start/completion/expiry latency, terminal outcomes, `might_stall` assignment-pressure evidence, telemetry contributors, forecast-event drops, and validation results for shadow scheduling estimates and unstarted-expiry forecasts. **Org 1 only — relies on `mimir-telemetry` recording rules.** |
| **Image Workers Overview** | `horde-image-workers-overview.json` | Per-worker drill-down — summary table, performance/requests/kudos time series, image capabilities (img2img, LoRA, max pixels), plus a Pop Diagnostics row (skip reasons + suspicion log lines). |
| **Text Workers Overview** | `horde-text-workers-overview.json` | Per-worker drill-down for text workers, plus the same Pop Diagnostics row. |
| **Image Worker Detail** | `horde-image-worker-detail.json` | Single image worker drill-down. |
| **Text Worker Detail** | `horde-text-worker-detail.json` | Single text worker drill-down. |
| **Image Models Overview** | `horde-image-models-overview.json` | All image models at a glance — aggregate stats, merged table with queue % and worker % shares, top 15 time series, domain-wide MPS throughput, and historical generation rankings. |
| **Text Models Overview** | `horde-text-models-overview.json` | All text models at a glance — aggregate stats, merged table with queue % and worker % shares, top 15 time series, domain-wide token throughput, and historical generation rankings. |
| **Image Model Detail** | `horde-image-model-detail.json` | Single image model drill-down — live stats, capacity share gauges (queue/worker/jobs), computed ratios over time, and historical generation stats with % of total. |
| **Text Model Detail** | `horde-text-model-detail.json` | Single text model drill-down — live stats, capacity share gauges (queue/worker/jobs), computed ratios over time, and historical generation stats with % of total. |
| **Horde App Traces (OTLP RED)** | `dashboards/ai_horde_otlp/horde-app-traces.json` | OTLP-derived RED metrics, Tempo service graph, and a generate→pop→submit funnel (span-metrics from `mimir-telemetry`). Cap query intervals to ≤24h — telemetry-tenant retention is 3 days. |

| **Horde Fleet Connectivity** | `dashboards/fleet/fleet-connectivity.json` | Outage-shape dashboard (Org 1, infra): WireGuard handshake age to/from the datastore host, edge HAProxy UP servers per backend host, redis-unreachable and error log rates per host (`loki-app`), request rate/p90/5xx per host (`mimir-telemetry`), and aihdb01 host/postgres health. Fixed colour per host on every panel; an "Edge: what users got" row (HAProxy frontend 5xx rate/share per edge, independent of app telemetry) and a shared-redis row (`job="redis"` from Alloy on aihdb01). Needs the `wireguard_peer_*` textfile metrics and aihdb01's pushed node metrics. |
| **Horde Tempo Read and Cache Health** | `dashboards/tempo/tempo-read-health.json` | Org 1, `mimir-infra`: external trace lookup traffic, GETs per lookup versus the previous day, p50/p95, 5xx/404 shares, metadata cache hits/errors/skips, shared Memcached memory/evictions/connections, Mimir cache performance, backend worker pressure, Garage descriptors and storage write failures. Requires `ops-tempo-read-*` rules and the staged tuning-intent/Memcached textfile metrics from ops. Uses metric queries only; does not add trace lookup traffic. |
| **Horde Anomalous Activity** | `dashboards/ai_horde_otlp/horde-anomalous-activity.json` | Org 1 only (shows IP addresses). Signals for scripted or abusive traffic: per-model daily image counts against their own 14-day median (`mimir-app`; the same statistic worker "top N" model selection reads), the month ranking and workers on its top models, accepted and rejected (4xx) image submissions and status polls per Client-Agent family at the edge (`mimir-infra`), anonymous volume and concentration (`mimir-app`), and the busiest anonymous IP subjects right now (`loki-app`, 15-minute window). The edge and anonymous panels read Loki recording-rule series; see [Anomalous Activity recording series](#anomalous-activity-recording-series). |

> **Note:** The legacy combined `horde-models.json` is superseded by the four model dashboards above.

### Anomalous Activity recording series

Counting edge requests per client straight from raw logs re-scans about 0.2 GB per hour of range on every refresh, which times out beyond
a few hours. The dashboard therefore reads per-minute counts that Loki recording rules write to Mimir (Loki ruler `remote_write`, the
rule's tenant sent as `X-Scope-OrgID`). Produce these series to use those panels; history starts when the rules start.

| Series (Mimir tenant) | Labels | Value |
|---|---|---|
| `horde:edge_image_submissions:count1m` (infrastructure) | `client`, `status_class` (`2xx`…) | `POST /api/v2/generate/async` lines per minute; top 50 `client`/`status_class` pairs per evaluation; `(none)` when no Client-Agent |
| `horde:edge_image_submissions_all:count1m` (infrastructure) | `status_class` | Same, all clients |
| `horde:edge_status_polls:count1m` (infrastructure) | `client` | `GET /api/v2/generate/status/` lines per minute, top 50 clients |
| `horde:edge_status_polls_all:count1m` (infrastructure) | — | Same, all clients |
| `horde:anon_generations:count5m` (ai-horde-app) | — | `(requesting user Anonymous#0 [` settlement lines per 5 minutes |
| `horde:anon_subjects:count5m` (ai-horde-app) | — | Distinct anonymous IP subjects (IPv4, or IPv6 /64) per 5 minutes |
| `horde:anon_top_subject_generations:max5m` (ai-horde-app) | — | Settlement lines of the busiest single subject per 5 minutes |

The edge series assume HAProxy `httplog` lines in Loki under `service_name="haproxy"` with the Client-Agent `name:version` captured as a
`{...}` block before the request line (`http-request capture req.hdr(Client-Agent),field(1,:,2) len 64`). Record each window from one
minute in the past (`offset 1m`) so lines still being shipped are counted. No IP address is stored in a metric; the IP table queries Loki.

### Public-org variants (`-public.json`)

A dashboard whose basename ends `-public.json` is the variant the deployments role provisions to the **public Grafana org (Org 2)**. The public org has `mimir-public` by default. Raw logs, traces and profiles require explicit opt-in through the deployments role's public datasource flags; production disables those three datasources. There is no public Alertmanager, `mimir-telemetry`, or `loki-app`. Bare-name dashboards that reference Org-1-only datasources should ship a `-public.json` sibling that drops or substitutes those panels. See `roles/horde_monitoring/tasks/dashboards.yml` in the deployments repo for the routing rules.

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
