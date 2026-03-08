# horde-exporters

Stats exporters implementations for the AI-Horde and related applications

## ai_horde_stats_exporter

Prometheus-compatible stats exporter for the AI-Horde. It exposes various application statistics that are also available through different endpoints of the AI-Horde API:

- `/status/models?type={model_type}`
- `/workers?type={worker_type}`
- `/status/performance`

See the [AI-Horde API docs](https://aihorde.net/api/) for detailed information on the available endpoints and their responses.
