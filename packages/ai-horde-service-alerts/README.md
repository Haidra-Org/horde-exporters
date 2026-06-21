# ai-horde-service-alerts

FastAPI **status-page backend** for the AI Horde. It computes per-component
health, persists status history / incidents / maintenance windows in Postgres,
and serves a public status API — while keeping the monitoring stack
(Alertmanager + Mimir) off the public internet.

## What it does

An in-process **status evaluator** runs on an interval and sets each
component's status to the *worst* of these signals:

1. an operator **override** (wins outright when present),
2. the latest **blackbox probe** pushed by [`horde-status-prober`](../horde-status-prober),
3. a curated **Alertmanager alert** (only alerts in `config/alert_component_map.yaml`
   can move a public component),
4. an active **maintenance window**.

With no signal a component stays `operational` during a startup grace window,
then becomes `unknown`. A **maintenance runner** activates/deactivates windows
on schedule, and a one-shot **Mimir backfill** can reconstruct history from the
`ALERTS` series on first deploy.

## API surface

| Surface | Routes | Auth |
| --- | --- | --- |
| Health | `GET /healthz` (liveness), `GET /readyz` (DB + upstream readiness) | none |
| Public | `GET /api/v1/public/{components,incidents,maintenance,history,stats}` | none (structural only — no alert prose/labels leak; `stats` serves a fixed allow-list of public-tenant throughput numbers, cached, never 5xx) |
| Internal | `GET/POST/PATCH /api/v1/internal/*` — incident/maintenance/override CRUD, raw Alertmanager/Mimir passthrough, `alerts/summary` (active, component-resolved) + `alerts/log` (firing+resolved over a window, from Mimir `ALERTS`) | moderator `apikey` (validated against AI Horde `GET /v2/find_user`) |
| Probe ingest | `POST /api/v1/internal/probe-results` | `x-prober-secret` shared secret |

## Configuration

All settings come from `HORDE_ALERTS_`-prefixed env vars (`extra="forbid"` —
unknown `HORDE_ALERTS_*` keys are rejected at startup). The most relevant:

| Env var | Default | Notes |
| --- | --- | --- |
| `HORDE_ALERTS_HOST` / `HORDE_ALERTS_PORT` | `0.0.0.0` / `19810` | Bind address/port. |
| `HORDE_ALERTS_DATABASE_URL` | local Postgres | SQLAlchemy async URL (`postgresql+asyncpg://…` or `sqlite+aiosqlite://…`). |
| `HORDE_ALERTS_ENABLE_DB` | `true` | When `false`, runs DB-less (entrypoint skips migrations; status features disabled). |
| `HORDE_ALERTS_ALERTMANAGER_BASE_URL` | `http://127.0.0.1:9093` | Alertmanager root. |
| `HORDE_ALERTS_MIMIR_BASE_URL` | `http://127.0.0.1:9009` | Mimir root (not `/prometheus`). |
| `HORDE_ALERTS_MIMIR_TENANT_DEFAULT` | `ai-horde-public` | `X-Scope-OrgID` for curated queries. |
| `HORDE_ALERTS_AIHORDE_BASE_URL` | `https://aihorde.net/api/` | Moderator verifier (trailing `/api/`). |
| `HORDE_ALERTS_PROBER_SHARED_SECRET` | unset | Enables `POST /probe-results`; unset ⇒ ingestion disabled (503). |
| `HORDE_ALERTS_STATUS_EVALUATOR_INTERVAL_SECONDS` | `15` | Evaluator tick. |
| `HORDE_ALERTS_NO_SIGNAL_GRACE_SECONDS` | `900` | Grace before a no-signal component becomes `unknown`. |
| `HORDE_ALERTS_BACKFILL_ON_STARTUP` / `_WINDOW_DAYS` | `false` / `90` | One-shot Mimir backfill. |
| `HORDE_ALERTS_ENABLE_BACKGROUND_TASKS` | `true` | Run evaluator + maintenance runner (off in unit tests). |
| `HORDE_ALERTS_ENABLE_INTERNAL_SWAGGER_DOCS` | `true` | Expose `/docs`, `/redoc`, `/openapi.json`. |

Component registry and alert→component mapping are files, not env vars:
`config/components.yaml` and `config/alert_component_map.yaml` (overridable via
`HORDE_ALERTS_COMPONENTS_CONFIG_PATH` / `HORDE_ALERTS_ALERT_COMPONENT_MAP_PATH`).

## Database & migrations

The schema is managed by Alembic (`migrations/`). The container entrypoint
runs `alembic upgrade head` before starting the app (skipped when
`HORDE_ALERTS_ENABLE_DB=false`). To run migrations manually:

```bash
HORDE_ALERTS_DATABASE_URL=postgresql+asyncpg://user:pw@host:5432/horde_status \
  uv run alembic upgrade head
```

## Running

```bash
uv sync
uv run ai-horde-service-alerts        # serves on :19810
```

## Docker

```bash
# build from this package directory
docker build -f Dockerfile -t ai-horde-service-alerts .
```

The image ships `config/`, `migrations/`, and `alembic.ini`; its entrypoint
migrates then launches uvicorn. Published as
`ghcr.io/haidra-org/ai-horde-service-alerts` on push to `main`.

## Tests

```bash
uv run pytest          # unit + integration (SQLite-backed)
uv run ruff check src tests
uv run zuban check
```

The Alembic **parity test** (`tests/integration/test_migrations.py`) applies
the migration to a real Postgres and asserts the schema matches the ORM models.
It is skipped unless a Postgres URL is provided:

```bash
docker run --rm -d --name pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=horde_status \
  -p 5433:5432 postgres:16-alpine
HORDE_ALERTS_TEST_DATABASE_URL=postgresql+asyncpg://postgres:pw@127.0.0.1:5433/horde_status \
  uv run pytest tests/integration/test_migrations.py
```
