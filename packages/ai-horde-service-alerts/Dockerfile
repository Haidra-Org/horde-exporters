# syntax=docker/dockerfile:1.7
#
# Build context: the ai-horde-service-alerts package directory
# (packages/ai-horde-service-alerts). Build from the repo root with:
#
#   docker build \
#     -f packages/ai-horde-service-alerts/Dockerfile \
#     packages/ai-horde-service-alerts

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.11.2

# ─── Builder ────────────────────────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM python:${PYTHON_VERSION}-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    VIRTUAL_ENV=/opt/venv

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /src
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY config ./config
COPY migrations ./migrations

RUN uv venv "${VIRTUAL_ENV}" \
    && uv pip install --python "${VIRTUAL_ENV}/bin/python" .

# ─── Runtime ────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HORDE_ALERTS_HOST=0.0.0.0 \
    HORDE_ALERTS_PORT=19810

RUN groupadd --system --gid 1000 horde \
    && useradd --system --uid 1000 --gid horde --no-create-home --shell /usr/sbin/nologin horde

COPY --from=builder /opt/venv /opt/venv

# Copy config files needed at runtime for component seeding and alert mapping.
# The app reads these from the filesystem, not from env vars.
COPY --from=builder /src/config /opt/config

# Alembic config + migration scripts. The entrypoint runs `alembic upgrade head`
# from /opt/app, so alembic.ini (script_location = migrations) resolves here.
COPY --from=builder /src/alembic.ini /opt/app/alembic.ini
COPY --from=builder /src/migrations /opt/app/migrations

# Entrypoint runs Alembic migrations before starting the app.
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Config paths default to locations relative to the package root.
# Override so they work inside the container.
ENV HORDE_ALERTS_COMPONENTS_CONFIG_PATH=/opt/config/components.yaml \
    HORDE_ALERTS_ALERT_COMPONENT_MAP_PATH=/opt/config/alert_component_map.yaml

USER horde
WORKDIR /opt/app
EXPOSE 19810

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:19810/healthz')" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
