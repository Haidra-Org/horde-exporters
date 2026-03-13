"""Configuration and settings for the AI Horde Stats Exporter."""

import logging

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

RATE_LIMIT_BACKOFF_THRESHOLD = 10


class ScrapeIntervalsSettings(BaseModel):
    models: int = 8
    workers: int = 300
    performance: int = 2
    stats: int = 120
    modes: int = 30
    teams: int = 300


class ApiSettings(BaseModel):
    base_url: str = "https://aihorde.net/api/v2"
    user_agent: str = "horde_prometheus_exporter"
    timeout: int = 10


class ExporterSettings(BaseModel):
    port: int = 9150
    log_level: str = "INFO"


class ZeroOmissionSettings(BaseModel):
    """Fields that should only be written when non-zero.

    Zero values are semantically "null" for these fields.
    Omitting them reduces cardinality and storage in Prometheus considerably,
    while still conveying the same information (zero = no activity).
    """

    models: list[str] = ["queued", "jobs", "eta"]
    workers: list[str] = ["uncompleted_jobs"]
    performance: list[str] = ["queued_forms", "queued_requests", "queued_text_requests"]
    stats: list[str] = []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HORDE_",
        env_nested_delimiter="__",
        env_ignore_empty=True,
        yaml_file="exporter_config.yaml",
        yaml_file_encoding="utf-8",
    )

    scrape_intervals: ScrapeIntervalsSettings = ScrapeIntervalsSettings()
    api: ApiSettings = ApiSettings()
    exporter: ExporterSettings = ExporterSettings()
    zero_omission: ZeroOmissionSettings = ZeroOmissionSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
        )


def load_settings(config_path: str = "exporter_config.yaml") -> Settings:
    """Create a Settings instance using the given YAML config file path."""

    class _Settings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="HORDE_",
            env_nested_delimiter="__",
            env_ignore_empty=True,
            yaml_file=config_path,
            yaml_file_encoding="utf-8",
        )

    return _Settings()
