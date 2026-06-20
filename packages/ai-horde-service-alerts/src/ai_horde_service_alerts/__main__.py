"""Console entry point that runs the FastAPI app via uvicorn."""

from __future__ import annotations

import os

import uvicorn


def run() -> None:
    """Run the FastAPI service. Configurable via ``HORDE_ALERTS_HOST`` / ``_PORT``."""
    host = os.environ.get("HORDE_ALERTS_HOST", "0.0.0.0")
    port = int(os.environ.get("HORDE_ALERTS_PORT", "19810"))

    from ai_horde_service_alerts import create_app

    uvicorn.run(
        create_app,
        factory=True,
        host=host,
        port=port,
        log_level=os.environ.get("HORDE_ALERTS_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    run()
