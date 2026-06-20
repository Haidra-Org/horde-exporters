FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

# Healthz port (override via HORDE_PROBER_HEALTHZ_PORT)
EXPOSE 8081

# Drop privileges
RUN useradd --system --uid 1001 prober
USER prober

ENTRYPOINT ["horde-status-prober"]
