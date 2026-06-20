# horde-status-prober

External blackbox prober for the AI Horde status page. Each probe runs on
its own interval, hits the public AI Horde API (or webhook surface),
classifies the result as `ok` / `degraded` / `down`, and POSTs it to
`ai-horde-service-alerts` via `POST /api/v1/internal/probe-results` using
a shared-secret header (`x-prober-secret`).

## Layout

| Path | Purpose |
| ---- | ------- |
| `src/horde_status_prober/config.py` | `pydantic-settings` env loader (`HORDE_PROBER_*`). |
| `src/horde_status_prober/probes/` | Probe implementations + `Probe` protocol. |
| `src/horde_status_prober/pusher.py` | HTTP client that POSTs results to the alerts service. |
| `src/horde_status_prober/main.py` | APScheduler + `/healthz` FastAPI server (CLI entrypoint). |

## Probes

| name | component | upstream | notes |
| ---- | --------- | -------- | ----- |
| `api_heartbeat` | `api` | `GET /v2/status/heartbeat` | latency-graded |
| `api_performance` | `api` | `GET /v2/status/performance` | parses JSON, latency-graded |
| `image_workers` | `image` | `GET /v2/status/performance` | reads `worker_count` |
| `text_workers` | `text` | `GET /v2/status/performance` | reads `text_worker_count` |
| `webhooks_smoke` | `webhooks` | `GET /v2/status/heartbeat` | placeholder until a dedicated webhook surface exists |
| `alchemy_smoke` | `alchemy` | `GET /v2/workers?type=interrogation` | counts `online: true` workers |

## Required configuration

| env | default | meaning |
| --- | ------- | ------- |
| `HORDE_PROBER_PROBER_SHARED_SECRET` | _required_ | Shared secret matching the alerts service. |
| `HORDE_PROBER_AIHORDE_BASE_URL` | `https://aihorde.net/api` | AI Horde API root. |
| `HORDE_PROBER_ALERTS_BASE_URL` | `https://alerts.haidra.net/api/v1` | Alerts service root. |
| `HORDE_PROBER_*_INTERVAL` | per-probe defaults | Override probe intervals (seconds). |
| `HORDE_PROBER_HEALTHZ_PORT` | `8081` | Bind port for the `/healthz` endpoint. |

## Running locally

```bash
cd horde-exporters/packages/horde-status-prober
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
HORDE_PROBER_PROBER_SHARED_SECRET=devsecret \
HORDE_PROBER_AIHORDE_BASE_URL=https://aihorde.net/api \
HORDE_PROBER_ALERTS_BASE_URL=http://127.0.0.1:8000/api/v1 \
  .venv/bin/horde-status-prober
```

`GET http://localhost:8081/healthz` returns

```json
{"status":"ok","consecutive_failures":0}
```

and flips to `"degraded"` once `max_consecutive_push_failures` is reached.
