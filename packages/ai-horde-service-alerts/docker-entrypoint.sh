#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Haidra Contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

# ── Database migrations ─────────────────────────────────────────────────────
# Run Alembic migrations before starting the app.  Uses the same
# HORDE_ALERTS_DATABASE_URL that the FastAPI app reads (already
# exported in the Dockerfile or supplied via the Compose env_file).
# If ENABLE_DB is explicitly "false" we skip migrations entirely —
# the app will run in DB-less mode and /healthz still passes.
if [[ "${HORDE_ALERTS_ENABLE_DB:-true}" != "false" ]]; then
    echo "HORDE_ALERTS_ENABLE_DB=${HORDE_ALERTS_ENABLE_DB:-true} — running migrations"
    alembic upgrade head
else
    echo "HORDE_ALERTS_ENABLE_DB=false — skipping migrations"
fi

# ── Start the FastAPI service ───────────────────────────────────────────────
echo "Starting ai-horde-service-alerts..."
exec ai-horde-service-alerts
