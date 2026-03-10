# horde-exporters

Stats exporters implementations for the AI-Horde and related applications

## ai_horde_stats_exporter

Prometheus-compatible stats exporter for the AI-Horde. It exposes various application statistics that are also available through different endpoints of the AI-Horde API:

- `/status/models?type={model_type}`
- `/workers?type={worker_type}`
- `/status/performance`

See the [AI-Horde API docs](https://aihorde.net/api/) for detailed information on the available endpoints and their responses.

## Grafana Dashboards

Pre-built Grafana dashboards are included in the `dashboards/` directory. They require a Prometheus datasource pointed at this exporter (default port `9150`).

| Dashboard | File | Description |
|-----------|------|-------------|
| **Horde Performance** | `dashboards/horde-performance.json` | High-level stats — API status, image/text worker counts, throughput (MPS/min, tokens/min), queue depth, historical generation stats, queue drain estimates, and teams overview. |
| **Horde Workers** | `dashboards/horde-workers.json` | Per-worker drill-down — summary table, performance/requests/kudos time series, image capabilities (img2img, LoRA, max pixels), text capabilities (max length, context length, tokens generated). |
| **Image Models Overview** | `dashboards/horde-image-models-overview.json` | All image models at a glance — aggregate stats, merged table with queue % and worker % shares, top 15 time series, domain-wide MPS throughput, and historical generation rankings. |
| **Text Models Overview** | `dashboards/horde-text-models-overview.json` | All text models at a glance — aggregate stats, merged table with queue % and worker % shares, top 15 time series, domain-wide token throughput, and historical generation rankings. |
| **Image Model Detail** | `dashboards/horde-image-model-detail.json` | Single image model drill-down — live stats, capacity share gauges (queue/worker/jobs), computed ratios over time (queue per worker, worker share %, queue share %), and historical generation stats with % of total. |
| **Text Model Detail** | `dashboards/horde-text-model-detail.json` | Single text model drill-down — live stats, capacity share gauges (queue/worker/jobs), computed ratios over time (queue per worker, worker share %, queue share %), and historical generation stats with % of total. |

> **Note:** The legacy combined `dashboards/horde-models.json` is superseded by the four model dashboards above.

### Importing

1. In Grafana, go to **Dashboards → New → Import**.
2. Upload or paste the JSON from any of the files above.
3. Select your Prometheus datasource when prompted.

All dashboards share a `horde` tag and cross-link via the dashboard navigation dropdown. The overview dashboards have hardcoded type filters; the detail dashboards provide a `model` template variable for drill-down.
