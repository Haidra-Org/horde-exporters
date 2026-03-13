"""Entry point for ``python -m ai_horde_stats_exporter`` and the ``horde-exporter`` console script."""

import argparse
import logging
import os

from .config import load_settings
from .exporter import HordeExporter

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prometheus-compatible stats exporter for the AI-Horde",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to the YAML config file (default: exporter_config.yaml). "
        "Can also be set via the HORDE_CONFIG_PATH environment variable.",
    )
    args = parser.parse_args()

    config_path = args.config or os.environ.get(
        "HORDE_CONFIG_PATH",
        "exporter_config.yaml",
    )

    config = load_settings(config_path)
    logger.info(f"Loaded config: {config.model_dump()}")

    exporter = HordeExporter(config)
    exporter.start()


if __name__ == "__main__":
    main()
