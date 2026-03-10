"""
AI Horde Stats Exporter - Prometheus compatible metrics exporter for the AI-Horde Application

Scrapes AI Horde APIs and exposes metrics in Prometheus format
"""

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import requests
from prometheus_client import start_http_server, Gauge, Counter
from pydantic import BaseModel, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Configuration


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
    while still conveying the same information (zero = no activity)."""

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


# Threshold at which we start delaying requests to avoid hitting the limit
RATE_LIMIT_BACKOFF_THRESHOLD = 10


@dataclass
class RateLimitState:
    """Thread-safe tracker for API rate-limit headers."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    limit: int = 0
    remaining: int = 0
    reset_epoch: float = 0.0
    retry_after: float = 0.0
    last_updated: float = 0.0

    def update_from_headers(self, headers: dict) -> None:
        with self._lock:
            self.limit = int(headers.get("x-ratelimit-limit", self.limit))
            self.remaining = int(headers.get("x-ratelimit-remaining", self.remaining))
            self.reset_epoch = float(headers.get("x-ratelimit-reset", self.reset_epoch))
            self.retry_after = float(headers.get("retry-after", self.retry_after))
            self.last_updated = time.time()

    def seconds_until_reset(self) -> float:
        with self._lock:
            return max(0.0, self.reset_epoch - time.time())

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "limit": self.limit,
                "remaining": self.remaining,
                "seconds_until_reset": max(0.0, self.reset_epoch - time.time()),
            }


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------


class ApiModel(BaseModel):
    """Base for API response models. Strips null values so field defaults apply."""

    @model_validator(mode="before")
    @classmethod
    def _strip_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data


class HordeModelStatus(ApiModel):
    name: str
    count: int = 0
    queued: float = 0
    performance: float = 0
    jobs: float = 0
    eta: int = 0


class WorkerKudosDetails(ApiModel):
    generated: float = 0
    uptime: float = 0


class HordeWorker(ApiModel):
    name: str
    online: bool = False
    requests_fulfilled: int = 0
    kudos_rewards: float = 0
    kudos_details: WorkerKudosDetails = WorkerKudosDetails()
    performance: str = "0"
    threads: int = 0
    models: list[str] = []
    uncompleted_jobs: int = 0
    uptime: int = 0
    maintenance_mode: bool = False
    trusted: bool = False
    flagged: bool = False
    nsfw: bool = False
    bridge_agent: str = "unknown"
    # image-specific
    max_pixels: int = 0
    megapixelsteps_generated: float = 0
    img2img: bool = False
    painting: bool = False
    lora: bool = False
    # text-specific
    max_length: int = 0
    max_context_length: int = 0
    tokens_generated: float = 0

    @property
    def parsed_performance(self) -> float:
        if isinstance(self.performance, (int, float)):
            return float(self.performance)
        try:
            return float(self.performance.split()[0])
        except (ValueError, IndexError):
            return 0.0

    @property
    def model_count(self) -> int:
        return len(self.models)


class PerformanceStatus(ApiModel):
    queued_requests: int = 0
    queued_text_requests: int = 0
    worker_count: int = 0
    text_worker_count: int = 0
    interrogator_count: int = 0
    thread_count: int = 0
    text_thread_count: int = 0
    interrogator_thread_count: int = 0
    past_minute_megapixelsteps: float = 0
    past_minute_tokens: float = 0
    queued_megapixelsteps: float = 0
    queued_tokens: float = 0
    queued_forms: int = 0


class ImageStatsPeriod(ApiModel):
    images: int = 0
    ps: int = 0


class ImageStatsResponse(ApiModel):
    minute: ImageStatsPeriod = ImageStatsPeriod()
    hour: ImageStatsPeriod = ImageStatsPeriod()
    day: ImageStatsPeriod = ImageStatsPeriod()
    month: ImageStatsPeriod = ImageStatsPeriod()
    total: ImageStatsPeriod = ImageStatsPeriod()


class TextStatsPeriod(ApiModel):
    requests: int = 0
    tokens: int = 0


class TextStatsResponse(ApiModel):
    minute: TextStatsPeriod = TextStatsPeriod()
    hour: TextStatsPeriod = TextStatsPeriod()
    day: TextStatsPeriod = TextStatsPeriod()
    month: TextStatsPeriod = TextStatsPeriod()
    total: TextStatsPeriod = TextStatsPeriod()


class StatsModelsResponse(ApiModel):
    day: dict[str, int] = {}
    month: dict[str, int] = {}
    total: dict[str, int] = {}


class ModesResponse(ApiModel):
    maintenance_mode: bool = False
    invite_only_mode: bool = False
    raid_mode: bool = False


class HordeTeam(ApiModel):
    name: str = "unknown"
    requests_fulfilled: int = 0
    kudos: float = 0
    worker_count: int = 0


# ---------------------------------------------------------------------------
# Metric specifications
#
# Each FieldSpec maps one attribute on an API response model to a Prometheus
# gauge.  The generic emitter (_emit_fields) handles bool→int conversion,
# zero-omission checks, and conditional type filtering in one place.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """Maps a model attribute to a Prometheus gauge for per-entity export."""

    metric_name: str
    help_text: str
    attr: str
    condition: str | None = None


@dataclass(frozen=True)
class AggregateSpec:
    """Defines an aggregate metric computed across all entities of a collection."""

    metric_name: str
    help_text: str
    func: str  # "sum" | "count" | "mean" | "count_true"
    attr: str = ""
    condition: str | None = None


# Per-model fields (labels: model, type)
MODEL_FIELDS: list[FieldSpec] = [
    FieldSpec("horde_model_queued", "Queued requests for specific model", "queued"),
    FieldSpec(
        "horde_model_workers_count", "Active workers for specific model", "count"
    ),
    FieldSpec(
        "horde_model_performance", "Performance for specific model", "performance"
    ),
    FieldSpec("horde_model_jobs", "Active jobs for specific model", "jobs"),
    FieldSpec("horde_model_eta_seconds", "ETA for specific model", "eta"),
]

MODEL_AGGREGATES: list[AggregateSpec] = [
    AggregateSpec(
        "horde_models_queued_total",
        "Total queued requests across all models",
        "sum",
        "queued",
    ),
    AggregateSpec(
        "horde_models_workers_count",
        "Total active workers across all models",
        "sum",
        "count",
    ),
    AggregateSpec(
        "horde_models_performance_total",
        "Total performance across all models",
        "sum",
        "performance",
    ),
    AggregateSpec(
        "horde_models_jobs_total", "Total active jobs across all models", "sum", "jobs"
    ),
    AggregateSpec(
        "horde_models_eta_seconds_avg", "Average ETA across all models", "mean", "eta"
    ),
    AggregateSpec(
        "horde_models_active_total", "Count of distinct active models", "count"
    ),
]

# Per-worker fields (labels: worker, type)
WORKER_FIELDS: list[FieldSpec] = [
    FieldSpec(
        "horde_worker_requests_fulfilled_total",
        "Requests fulfilled by specific worker",
        "requests_fulfilled",
    ),
    FieldSpec(
        "horde_worker_kudos_rewards",
        "Kudos rewards for specific worker",
        "kudos_rewards",
    ),
    FieldSpec(
        "horde_worker_performance",
        "Performance of specific worker",
        "parsed_performance",
    ),
    FieldSpec("horde_worker_threads", "Thread count of specific worker", "threads"),
    FieldSpec(
        "horde_worker_kudos_generated_total",
        "Kudos generated by specific worker",
        "kudos_details.generated",
    ),
    FieldSpec(
        "horde_worker_kudos_uptime",
        "Kudos uptime for specific worker",
        "kudos_details.uptime",
    ),
    FieldSpec(
        "horde_worker_models_count",
        "Number of models supported by worker",
        "model_count",
    ),
    FieldSpec(
        "horde_worker_uncompleted_jobs",
        "Uncompleted jobs for specific worker",
        "uncompleted_jobs",
    ),
    FieldSpec(
        "horde_worker_uptime_seconds", "Total uptime of worker in seconds", "uptime"
    ),
    FieldSpec(
        "horde_worker_maintenance", "Worker is in maintenance mode", "maintenance_mode"
    ),
    FieldSpec("horde_worker_trusted", "Worker is trusted", "trusted"),
    FieldSpec("horde_worker_flagged", "Worker owner is flagged", "flagged"),
    FieldSpec("horde_worker_nsfw_enabled", "Worker accepts NSFW requests", "nsfw"),
    FieldSpec(
        "horde_worker_max_pixels",
        "Maximum pixels supported by worker",
        "max_pixels",
        condition="image",
    ),
    FieldSpec(
        "horde_worker_megapixelsteps_generated_total",
        "Megapixelsteps generated by worker",
        "megapixelsteps_generated",
        condition="image",
    ),
    FieldSpec(
        "horde_worker_img2img_enabled",
        "Worker supports img2img",
        "img2img",
        condition="image",
    ),
    FieldSpec(
        "horde_worker_painting_enabled",
        "Worker supports inpainting/outpainting",
        "painting",
        condition="image",
    ),
    FieldSpec(
        "horde_worker_lora_enabled", "Worker supports LoRA", "lora", condition="image"
    ),
    FieldSpec(
        "horde_worker_max_length",
        "Maximum response length for text worker",
        "max_length",
        condition="text",
    ),
    FieldSpec(
        "horde_worker_max_context_length",
        "Maximum context length for text worker",
        "max_context_length",
        condition="text",
    ),
    FieldSpec(
        "horde_worker_tokens_generated_total",
        "Total tokens generated by text worker",
        "tokens_generated",
        condition="text",
    ),
]

WORKER_AGGREGATES: list[AggregateSpec] = [
    AggregateSpec("horde_workers_active_total", "Total active workers", "count"),
    AggregateSpec(
        "horde_workers_performance_total",
        "Total worker performance",
        "sum",
        "parsed_performance",
    ),
    AggregateSpec(
        "horde_workers_threads_total", "Total worker threads", "sum", "threads"
    ),
    AggregateSpec(
        "horde_workers_uptime_seconds_total",
        "Sum of all worker uptimes in seconds",
        "sum",
        "uptime",
    ),
    AggregateSpec(
        "horde_workers_maintenance_total",
        "Workers in maintenance mode",
        "count_true",
        "maintenance_mode",
    ),
    AggregateSpec(
        "horde_workers_trusted_total", "Trusted workers count", "count_true", "trusted"
    ),
    AggregateSpec(
        "horde_workers_flagged_total", "Flagged workers count", "count_true", "flagged"
    ),
    AggregateSpec(
        "horde_workers_img2img_capable_total",
        "Image workers supporting img2img",
        "count_true",
        "img2img",
        condition="image",
    ),
    AggregateSpec(
        "horde_workers_lora_capable_total",
        "Image workers supporting LoRA",
        "count_true",
        "lora",
        condition="image",
    ),
    AggregateSpec(
        "horde_workers_avg_performance",
        "Average performance across online workers",
        "mean",
        "parsed_performance",
    ),
]

# Per-team fields (labels: team)
TEAM_FIELDS: list[FieldSpec] = [
    FieldSpec(
        "horde_team_requests_fulfilled",
        "Requests fulfilled by team workers",
        "requests_fulfilled",
    ),
    FieldSpec("horde_team_kudos", "Total kudos earned by team workers", "kudos"),
    FieldSpec("horde_team_worker_count", "Number of workers in team", "worker_count"),
]


# ---------------------------------------------------------------------------
# Prometheus metric registry
# ---------------------------------------------------------------------------


class HordeMetrics:
    """Creates and indexes every Prometheus metric the exporter produces.

    Per-entity and aggregate metrics are registered from the spec tables above.
    One-off metrics (performance, stats, modes, health) are registered inline.
    All metrics are accessed via gauge(name) / counter(name).
    """

    def __init__(self):
        self._gauges: dict[str, Gauge] = {}
        self._counters: dict[str, Counter] = {}

        # Per-entity metrics from spec tables
        for spec in MODEL_FIELDS:
            self._add_gauge(spec.metric_name, spec.help_text, ["model", "type"])
        for spec in MODEL_AGGREGATES:
            self._add_gauge(spec.metric_name, spec.help_text, ["type"])
        for spec in WORKER_FIELDS:
            self._add_gauge(spec.metric_name, spec.help_text, ["worker", "type"])
        for spec in WORKER_AGGREGATES:
            self._add_gauge(spec.metric_name, spec.help_text, ["type"])
        for spec in TEAM_FIELDS:
            self._add_gauge(spec.metric_name, spec.help_text, ["team"])

        # Worker info (extra label for bridge_agent metadata)
        self._add_gauge(
            "horde_worker_info",
            "Worker metadata (always 1, labels carry the info)",
            ["worker", "type", "bridge_agent"],
        )

        # Workers aggregate counter (API gives cumulative totals, not deltas)
        self._add_counter(
            "horde_workers_requests_fulfilled_total",
            "Total requests fulfilled by all workers",
            ["type"],
        )

        # Performance metrics (labels: type)
        for name, help_text in [
            ("horde_performance_queued_requests", "Queued requests"),
            ("horde_performance_worker_count", "Worker count"),
            ("horde_performance_thread_count", "Thread count"),
            (
                "horde_performance_past_minute_megapixelsteps",
                "Megapixelsteps in past minute",
            ),
            ("horde_performance_past_minute_tokens", "Tokens in past minute"),
            ("horde_performance_queued_megapixelsteps", "Queued megapixelsteps"),
            ("horde_performance_queued_tokens", "Queued tokens"),
            ("horde_performance_queued_forms", "Queued interrogation forms"),
            (
                "horde_performance_estimated_queue_drain_seconds",
                "Estimated seconds to drain the queue at current throughput",
            ),
            (
                "horde_performance_throughput_per_thread",
                "Throughput per thread in the past minute",
            ),
        ]:
            self._add_gauge(name, help_text, ["type"])

        # Stats metrics
        for name, help_text in [
            ("horde_stats_images_generated", "Total images generated in time period"),
            (
                "horde_stats_pixelsteps_generated",
                "Total pixelsteps generated in time period",
            ),
            (
                "horde_stats_text_requests_generated",
                "Total text requests generated in time period",
            ),
            ("horde_stats_tokens_generated", "Total tokens generated in time period"),
        ]:
            self._add_gauge(name, help_text, ["period"])

        for name, help_text in [
            (
                "horde_stats_model_images_generated",
                "Images generated per model in time period",
            ),
            (
                "horde_stats_model_texts_generated",
                "Text requests generated per model in time period",
            ),
        ]:
            self._add_gauge(name, help_text, ["model", "period"])

        # Mode / heartbeat
        for name, help_text in [
            ("horde_mode_maintenance", "Whether the horde is in maintenance mode"),
            ("horde_mode_invite_only", "Whether the horde is in invite-only mode"),
            ("horde_mode_raid", "Whether the horde is in raid mode"),
            ("horde_api_up", "Whether the AI Horde API is reachable"),
        ]:
            self._add_gauge(name, help_text)

        # Teams aggregate
        self._add_gauge("horde_teams_total", "Total number of teams")

        # Exporter health
        self._add_gauge(
            "horde_exporter_scrape_success",
            "Whether the last scrape was successful",
            ["endpoint"],
        )
        self._add_gauge(
            "horde_exporter_scrape_duration_seconds",
            "Duration of the last scrape",
            ["endpoint"],
        )
        self._add_gauge(
            "horde_exporter_ratelimit_limit", "Rate-limit ceiling reported by the API"
        )
        self._add_gauge(
            "horde_exporter_ratelimit_remaining",
            "Requests remaining in the current rate-limit window",
        )
        self._add_gauge(
            "horde_exporter_ratelimit_reset_seconds",
            "Seconds until the rate-limit window resets",
        )
        self._add_counter(
            "horde_exporter_ratelimit_backoff_total",
            "Times the exporter delayed a request due to rate-limit proximity",
        )

    def _add_gauge(
        self, name: str, help_text: str, labels: list[str] | None = None
    ) -> Gauge:
        g = Gauge(name, help_text, labels or [])
        self._gauges[name] = g
        return g

    def _add_counter(
        self, name: str, help_text: str, labels: list[str] | None = None
    ) -> Counter:
        c = Counter(name, help_text, labels or [])
        self._counters[name] = c
        return c

    def gauge(self, name: str) -> Gauge:
        return self._gauges[name]

    def counter(self, name: str) -> Counter:
        return self._counters[name]


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------


class HordeExporter:
    """Main exporter class"""

    def __init__(self, config: Settings):
        self.config = config
        self.metrics = HordeMetrics()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.config.api.user_agent})
        self.rate_limit = RateLimitState()

    # --- networking ---

    def _wait_for_rate_limit(self):
        """Block if we're close to exhausting the rate-limit budget."""
        snap = self.rate_limit.snapshot()
        if snap["limit"] == 0:
            return

        if snap["remaining"] <= RATE_LIMIT_BACKOFF_THRESHOLD:
            wait = snap["seconds_until_reset"] + 0.5
            if wait > 0:
                logger.warning(
                    "Rate-limit low (%d/%d remaining), backing off %.1fs until reset",
                    snap["remaining"],
                    snap["limit"],
                    wait,
                )
                self.metrics.counter("horde_exporter_ratelimit_backoff_total").inc()
                time.sleep(wait)

    def _update_rate_limit_metrics(self):
        snap = self.rate_limit.snapshot()
        self.metrics.gauge("horde_exporter_ratelimit_limit").set(snap["limit"])
        self.metrics.gauge("horde_exporter_ratelimit_remaining").set(snap["remaining"])
        self.metrics.gauge("horde_exporter_ratelimit_reset_seconds").set(
            snap["seconds_until_reset"]
        )

    def fetch_api(self, endpoint: str) -> Any:
        """Fetch data from AI Horde API with fallback to stablehorde.net"""
        self._wait_for_rate_limit()

        primary_url = f"{self.config.api.base_url}{endpoint}"
        fallback_url = f"https://stablehorde.net/api/v2{endpoint}"

        try:
            response = self.session.get(primary_url, timeout=self.config.api.timeout)
            self.rate_limit.update_from_headers(response.headers)
            self._update_rate_limit_metrics()
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Error fetching from primary URL {primary_url}: {e}")
            logger.info(f"Attempting fallback to {fallback_url}")

            try:
                response = self.session.get(
                    fallback_url, timeout=self.config.api.timeout
                )
                self.rate_limit.update_from_headers(response.headers)
                self._update_rate_limit_metrics()
                response.raise_for_status()
                logger.info("Successfully fetched from fallback URL")
                return response.json()
            except Exception as fallback_error:
                logger.error(
                    f"Error fetching from fallback URL {fallback_url}: {fallback_error}"
                )
                raise

    # --- scrape bookkeeping ---

    def _record_scrape_success(self, endpoint: str, start_time: float):
        self.metrics.gauge("horde_exporter_scrape_success").labels(
            endpoint=endpoint
        ).set(1)
        self.metrics.gauge("horde_exporter_scrape_duration_seconds").labels(
            endpoint=endpoint
        ).set(time.time() - start_time)

    def _record_scrape_failure(self, endpoint: str):
        self.metrics.gauge("horde_exporter_scrape_success").labels(
            endpoint=endpoint
        ).set(0)

    # --- zero-omission ---

    def should_write_metric(
        self, metric_type: str, field_name: str, value: float
    ) -> bool:
        """Check whether a metric value should be emitted.

        Non-zero values are always written.  Zero values are suppressed for
        fields listed in the zero_omission config for the given metric_type.
        """
        if value != 0 and value != 0.0:
            return True
        nullable_fields = getattr(self.config.zero_omission, metric_type, [])
        return field_name not in nullable_fields

    # --- generic helpers ---

    @staticmethod
    def _resolve_attr(obj: object, path: str) -> Any:
        """Resolve a dotted attribute path (e.g. 'kudos_details.generated')."""
        for part in path.split("."):
            obj = getattr(obj, part)
        return obj

    def _emit_fields(
        self,
        specs: list[FieldSpec],
        entity: ApiModel,
        labels: dict[str, str],
        zero_omit_category: str,
        entity_type: str | None = None,
    ):
        """Emit per-entity Prometheus gauges from a model's attributes.

        bool fields are automatically converted to 0/1.
        """
        for spec in specs:
            if spec.condition and spec.condition != entity_type:
                continue

            raw = self._resolve_attr(entity, spec.attr)
            value = int(raw) if isinstance(raw, bool) else float(raw)

            if not self.should_write_metric(zero_omit_category, spec.attr, value):
                continue

            self.metrics.gauge(spec.metric_name).labels(**labels).set(value)

    def _emit_aggregates(
        self,
        specs: list[AggregateSpec],
        entities: Sequence[ApiModel],
        type_label: str,
    ):
        """Compute and emit aggregate metrics across a list of entities."""
        for spec in specs:
            if spec.condition and spec.condition != type_label:
                continue

            if spec.func == "count":
                value = float(len(entities))
            elif spec.func == "sum":
                value = sum(float(self._resolve_attr(e, spec.attr)) for e in entities)
            elif spec.func == "mean":
                value = (
                    sum(float(self._resolve_attr(e, spec.attr)) for e in entities)
                    / len(entities)
                    if entities
                    else 0.0
                )
            elif spec.func == "count_true":
                value = float(
                    sum(1 for e in entities if self._resolve_attr(e, spec.attr))
                )
            else:
                continue

            self.metrics.gauge(spec.metric_name).labels(type=type_label).set(value)

    # --- collectors ---

    def collect_models(self, model_type: str = "image"):
        endpoint = f"/status/models?type={model_type}"
        start_time = time.time()

        try:
            models = [HordeModelStatus(**m) for m in self.fetch_api(endpoint)]
            logger.info(f"Fetched {len(models)} {model_type} models")

            self._emit_aggregates(MODEL_AGGREGATES, models, model_type)

            for model in models:
                labels = {"model": model.name, "type": model_type}
                self._emit_fields(MODEL_FIELDS, model, labels, "models", model_type)

            logger.info(f"Collected metrics for {len(models)} {model_type} models")
            self._record_scrape_success(endpoint, start_time)

        except Exception as e:
            logger.error(f"Error collecting {model_type} models: {e}")
            self._record_scrape_failure(endpoint)

    def collect_workers(self, worker_type: str = "image"):
        endpoint = f"/workers?type={worker_type}"
        start_time = time.time()

        try:
            all_workers = [HordeWorker(**w) for w in self.fetch_api(endpoint)]
            online_workers = [w for w in all_workers if w.online]
            logger.info(
                f"Fetched {len(online_workers)} online {worker_type} workers "
                f"(out of {len(all_workers)} total)"
            )

            self._emit_aggregates(WORKER_AGGREGATES, online_workers, worker_type)

            for worker in online_workers:
                labels = {"worker": worker.name, "type": worker_type}
                self._emit_fields(WORKER_FIELDS, worker, labels, "workers", worker_type)

                self.metrics.gauge("horde_worker_info").labels(
                    worker=worker.name,
                    type=worker_type,
                    bridge_agent=worker.bridge_agent,
                ).set(1)

            logger.info(
                f"Collected metrics for {len(online_workers)} {worker_type} workers"
            )
            self._record_scrape_success(endpoint, start_time)

        except Exception as e:
            logger.error(f"Error collecting {worker_type} workers: {e}")
            self._record_scrape_failure(endpoint)

    def collect_performance(self):
        endpoint = "/status/performance"
        start_time = time.time()

        try:
            perf = PerformanceStatus(**self.fetch_api(endpoint))
            logger.info("Fetched performance metrics")

            g = self.metrics.gauge

            # Queued metrics (with zero-omission)
            if self.should_write_metric(
                "performance", "queued_requests", perf.queued_requests
            ):
                g("horde_performance_queued_requests").labels(type="image").set(
                    perf.queued_requests
                )
            if self.should_write_metric(
                "performance", "queued_text_requests", perf.queued_text_requests
            ):
                g("horde_performance_queued_requests").labels(type="text").set(
                    perf.queued_text_requests
                )
            if self.should_write_metric(
                "performance", "queued_forms", perf.queued_forms
            ):
                g("horde_performance_queued_forms").labels(type="interrogator").set(
                    perf.queued_forms
                )

            # Counts and throughput
            g("horde_performance_worker_count").labels(type="image").set(
                perf.worker_count
            )
            g("horde_performance_worker_count").labels(type="text").set(
                perf.text_worker_count
            )
            g("horde_performance_worker_count").labels(type="interrogator").set(
                perf.interrogator_count
            )
            g("horde_performance_thread_count").labels(type="image").set(
                perf.thread_count
            )
            g("horde_performance_thread_count").labels(type="text").set(
                perf.text_thread_count
            )
            g("horde_performance_thread_count").labels(type="interrogator").set(
                perf.interrogator_thread_count
            )
            g("horde_performance_past_minute_megapixelsteps").labels(type="image").set(
                perf.past_minute_megapixelsteps
            )
            g("horde_performance_past_minute_tokens").labels(type="text").set(
                perf.past_minute_tokens
            )
            g("horde_performance_queued_megapixelsteps").labels(type="image").set(
                perf.queued_megapixelsteps
            )
            g("horde_performance_queued_tokens").labels(type="text").set(
                perf.queued_tokens
            )

            # Synthesized: estimated queue drain time and throughput per thread
            if perf.past_minute_megapixelsteps > 0:
                g("horde_performance_estimated_queue_drain_seconds").labels(
                    type="image"
                ).set(perf.queued_megapixelsteps / perf.past_minute_megapixelsteps * 60)
            if perf.thread_count > 0:
                g("horde_performance_throughput_per_thread").labels(type="image").set(
                    perf.past_minute_megapixelsteps / perf.thread_count
                )

            if perf.past_minute_tokens > 0:
                g("horde_performance_estimated_queue_drain_seconds").labels(
                    type="text"
                ).set(perf.queued_tokens / perf.past_minute_tokens * 60)
            if perf.text_thread_count > 0:
                g("horde_performance_throughput_per_thread").labels(type="text").set(
                    perf.past_minute_tokens / perf.text_thread_count
                )

            self._record_scrape_success(endpoint, start_time)

        except Exception as e:
            logger.error(f"Error collecting performance: {e}")
            self._record_scrape_failure(endpoint)

    def collect_stats_totals(self):
        g = self.metrics.gauge

        # Image stats
        endpoint = "/stats/img/totals"
        start_time = time.time()
        try:
            stats = ImageStatsResponse(**self.fetch_api(endpoint))
            for period_name in ("minute", "hour", "day", "month", "total"):
                period = getattr(stats, period_name)
                g("horde_stats_images_generated").labels(period=period_name).set(
                    period.images
                )
                g("horde_stats_pixelsteps_generated").labels(period=period_name).set(
                    period.ps
                )
            self._record_scrape_success(endpoint, start_time)
        except Exception as e:
            logger.error(f"Error collecting image stats totals: {e}")
            self._record_scrape_failure(endpoint)

        # Text stats
        endpoint = "/stats/text/totals"
        start_time = time.time()
        try:
            stats = TextStatsResponse(**self.fetch_api(endpoint))
            for period_name in ("minute", "hour", "day", "month", "total"):
                period = getattr(stats, period_name)
                g("horde_stats_text_requests_generated").labels(period=period_name).set(
                    period.requests
                )
                g("horde_stats_tokens_generated").labels(period=period_name).set(
                    period.tokens
                )
            self._record_scrape_success(endpoint, start_time)
        except Exception as e:
            logger.error(f"Error collecting text stats totals: {e}")
            self._record_scrape_failure(endpoint)

    def collect_stats_models(self):
        g = self.metrics.gauge

        for kind, endpoint, metric in [
            (
                "image",
                "/stats/img/models?model_state=known",
                "horde_stats_model_images_generated",
            ),
            ("text", "/stats/text/models", "horde_stats_model_texts_generated"),
        ]:
            start_time = time.time()
            try:
                stats = StatsModelsResponse(**self.fetch_api(endpoint))
                for period_name in ("day", "month", "total"):
                    for model_name, count in getattr(stats, period_name).items():
                        g(metric).labels(model=model_name, period=period_name).set(
                            count
                        )
                self._record_scrape_success(endpoint, start_time)
            except Exception as e:
                logger.error(f"Error collecting stats models ({kind}): {e}")
                self._record_scrape_failure(endpoint)

    def collect_modes(self):
        g = self.metrics.gauge

        # Heartbeat
        hb_endpoint = "/status/heartbeat"
        try:
            self.fetch_api(hb_endpoint)
            g("horde_api_up").set(1)
            g("horde_exporter_scrape_success").labels(endpoint=hb_endpoint).set(1)
        except Exception:
            g("horde_api_up").set(0)
            g("horde_exporter_scrape_success").labels(endpoint=hb_endpoint).set(0)

        # Modes
        modes_endpoint = "/status/modes"
        start_time = time.time()
        try:
            modes = ModesResponse(**self.fetch_api(modes_endpoint))
            g("horde_mode_maintenance").set(int(modes.maintenance_mode))
            g("horde_mode_invite_only").set(int(modes.invite_only_mode))
            g("horde_mode_raid").set(int(modes.raid_mode))
            self._record_scrape_success(modes_endpoint, start_time)
        except Exception as e:
            logger.error(f"Error collecting modes: {e}")
            self._record_scrape_failure(modes_endpoint)

    def collect_teams(self):
        endpoint = "/teams"
        start_time = time.time()

        try:
            teams = [HordeTeam(**t) for t in self.fetch_api(endpoint)]
            self.metrics.gauge("horde_teams_total").set(len(teams))

            for team in teams:
                self._emit_fields(TEAM_FIELDS, team, {"team": team.name}, "teams")

            logger.info(f"Collected metrics for {len(teams)} teams")
            self._record_scrape_success(endpoint, start_time)

        except Exception as e:
            logger.error(f"Error collecting teams: {e}")
            self._record_scrape_failure(endpoint)

    # --- thread runners ---

    def run_models_collector(self):
        interval = self.config.scrape_intervals.models
        while True:
            try:
                self.collect_models("image")
                self.collect_models("text")
            except Exception as e:
                logger.error(f"Error in models collector: {e}")
            time.sleep(interval)

    def run_workers_collector(self):
        interval = self.config.scrape_intervals.workers
        while True:
            try:
                self.collect_workers("image")
                self.collect_workers("text")
            except Exception as e:
                logger.error(f"Error in workers collector: {e}")
            time.sleep(interval)

    def run_performance_collector(self):
        interval = self.config.scrape_intervals.performance
        while True:
            try:
                self.collect_performance()
            except Exception as e:
                logger.error(f"Error in performance collector: {e}")
            time.sleep(interval)

    def run_stats_collector(self):
        interval = self.config.scrape_intervals.stats
        while True:
            try:
                self.collect_stats_totals()
                self.collect_stats_models()
            except Exception as e:
                logger.error(f"Error in stats collector: {e}")
            time.sleep(interval)

    def run_modes_collector(self):
        interval = self.config.scrape_intervals.modes
        while True:
            try:
                self.collect_modes()
            except Exception as e:
                logger.error(f"Error in modes collector: {e}")
            time.sleep(interval)

    def run_teams_collector(self):
        interval = self.config.scrape_intervals.teams
        while True:
            try:
                self.collect_teams()
            except Exception as e:
                logger.error(f"Error in teams collector: {e}")
            time.sleep(interval)

    # --- startup ---

    def start(self):
        """Start the exporter"""
        port = self.config.exporter.port
        logger.info(f"Starting Horde exporter on port {port}")

        start_http_server(port)
        logger.info(f"Metrics available at http://localhost:{port}/metrics")

        threading.Thread(target=self.run_models_collector, daemon=True).start()
        threading.Thread(target=self.run_workers_collector, daemon=True).start()
        threading.Thread(target=self.run_performance_collector, daemon=True).start()
        threading.Thread(target=self.run_stats_collector, daemon=True).start()
        threading.Thread(target=self.run_modes_collector, daemon=True).start()
        threading.Thread(target=self.run_teams_collector, daemon=True).start()

        logger.info("All collectors started")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down exporter")


def main():
    config = Settings()
    logger.info(f"Loaded config: {config.model_dump()}")

    exporter = HordeExporter(config)
    exporter.start()


if __name__ == "__main__":
    main()
