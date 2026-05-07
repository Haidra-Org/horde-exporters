# syntax=docker/dockerfile:1.7
#
# Build context: the ai-horde-stats-exporter package directory
# (packages/ai-horde-stats-exporter). Build from the repo root with:
#
#   docker build \
#     -f packages/ai-horde-stats-exporter/Dockerfile \
#     packages/ai-horde-stats-exporter
#
# The package builds in isolation — it does not depend on the uv workspace
# root.

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
COPY pyproject.toml ./
COPY src ./src

RUN uv venv "${VIRTUAL_ENV}" \
    && uv pip install --python "${VIRTUAL_ENV}/bin/python" .

# ─── Runtime ────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HORDE_CONFIG_PATH=/etc/horde-stats-exporter/exporter_config.yaml

RUN groupadd --system --gid 1000 horde \
    && useradd --system --uid 1000 --gid horde --no-create-home --shell /usr/sbin/nologin horde \
    && mkdir -p /etc/horde-stats-exporter \
    && chown -R horde:horde /etc/horde-stats-exporter

COPY --from=builder /opt/venv /opt/venv

USER horde
WORKDIR /etc/horde-stats-exporter
EXPOSE 9150

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9150/metrics')" || exit 1

ENTRYPOINT ["horde-exporter"]
