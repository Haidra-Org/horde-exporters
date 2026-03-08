"""
AI Horde Stats Exporter - Prometheus compatible metrics exporter for the AI-Horde Application

Scrapes AI Horde APIs and exposes metrics in Prometheus format
"""

import logging
import threading
import time
from dataclasses import dataclass, field

import requests
from prometheus_client import start_http_server, Gauge, Counter
from pydantic import BaseModel
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


class ApiSettings(BaseModel):
    base_url: str = "https://aihorde.net/api/v2"
    user_agent: str = "horde_prometheus_exporter"
    timeout: int = 10


class ExporterSettings(BaseModel):
    port: int = 9100
    log_level: str = "INFO"


class ZeroOmissionSettings(BaseModel):
    """Fields that should only be written when non-zero.
    Zero values are semantically "null" for these fields.
    Omitting them reduces cardinality and storage in Prometheus considerably,
    while still conveying the same information (zero = no activity)."""

    models: list[str] = ["queued", "jobs", "eta"]
    workers: list[str] = ["uncompleted_jobs"]
    performance: list[str] = ["queued_forms", "queued_requests", "queued_text_requests"]


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


class HordeMetrics:
    """Container for all Prometheus metrics"""

    def __init__(self):
        # Model aggregates
        self.models_queued_total = Gauge(
            "horde_models_queued_total",
            "Total queued requests across all models",
            ["type"],
        )
        self.models_workers_count = Gauge(
            "horde_models_workers_count",
            "Total active workers across all models",
            ["type"],
        )
        self.models_performance = Gauge(
            "horde_models_performance_total",
            "Total performance across all models",
            ["type"],
        )
        self.models_jobs = Gauge(
            "horde_models_jobs_total", "Total active jobs across all models", ["type"]
        )
        self.models_eta_avg = Gauge(
            "horde_models_eta_seconds_avg", "Average ETA across all models", ["type"]
        )

        # Per-model metrics (top-N only)
        self.model_queued = Gauge(
            "horde_model_queued",
            "Queued requests for specific model",
            ["model", "type"],
        )
        self.model_workers_count = Gauge(
            "horde_model_workers_count",
            "Active workers for specific model",
            ["model", "type"],
        )
        self.model_performance = Gauge(
            "horde_model_performance",
            "Performance for specific model",
            ["model", "type"],
        )
        self.model_jobs = Gauge(
            "horde_model_jobs", "Active jobs for specific model", ["model", "type"]
        )
        self.model_eta_seconds = Gauge(
            "horde_model_eta_seconds", "ETA for specific model", ["model", "type"]
        )

        # Worker aggregates
        self.workers_active_total = Gauge(
            "horde_workers_active_total", "Total active workers", ["type"]
        )
        self.workers_performance_total = Gauge(
            "horde_workers_performance_total", "Total worker performance", ["type"]
        )
        self.workers_threads_total = Gauge(
            "horde_workers_threads_total", "Total worker threads", ["type"]
        )
        self.workers_requests_fulfilled_total = Counter(
            "horde_workers_requests_fulfilled_total",
            "Total requests fulfilled by all workers",
            ["type"],
        )

        # Per-worker metrics (top-N only)
        # Note: requests_fulfilled stored as Gauge (absolute value from API)
        # not Counter, since API provides cumulative totals not deltas
        self.worker_requests_fulfilled = Gauge(
            "horde_worker_requests_fulfilled_total",
            "Requests fulfilled by specific worker",
            ["worker", "type"],
        )
        self.worker_kudos_rewards = Gauge(
            "horde_worker_kudos_rewards",
            "Kudos rewards for specific worker",
            ["worker", "type"],
        )
        self.worker_performance = Gauge(
            "horde_worker_performance",
            "Performance of specific worker",
            ["worker", "type"],
        )
        self.worker_threads = Gauge(
            "horde_worker_threads",
            "Thread count of specific worker",
            ["worker", "type"],
        )
        self.worker_uncompleted_jobs = Gauge(
            "horde_worker_uncompleted_jobs",
            "Uncompleted jobs for specific worker",
            ["worker", "type"],
        )
        self.worker_kudos_generated = Gauge(
            "horde_worker_kudos_generated_total",
            "Kudos generated by specific worker",
            ["worker", "type"],
        )
        self.worker_kudos_uptime = Gauge(
            "horde_worker_kudos_uptime",
            "Kudos uptime for specific worker",
            ["worker", "type"],
        )
        self.worker_models_count = Gauge(
            "horde_worker_models_count",
            "Number of models supported by worker",
            ["worker", "type"],
        )
        self.worker_max_pixels = Gauge(
            "horde_worker_max_pixels",
            "Maximum pixels supported by worker",
            ["worker", "type"],
        )
        self.worker_megapixelsteps_generated = Gauge(
            "horde_worker_megapixelsteps_generated_total",
            "Megapixelsteps generated by worker",
            ["worker", "type"],
        )
        self.worker_max_length = Gauge(
            "horde_worker_max_length",
            "Maximum response length for text worker",
            ["worker", "type"],
        )
        self.worker_max_context_length = Gauge(
            "horde_worker_max_context_length",
            "Maximum context length for text worker",
            ["worker", "type"],
        )

        # Performance (global) metrics
        self.performance_queued_requests = Gauge(
            "horde_performance_queued_requests", "Queued requests", ["type"]
        )
        self.performance_worker_count = Gauge(
            "horde_performance_worker_count", "Worker count", ["type"]
        )
        self.performance_thread_count = Gauge(
            "horde_performance_thread_count", "Thread count", ["type"]
        )
        self.performance_past_minute_megapixelsteps = Gauge(
            "horde_performance_past_minute_megapixelsteps",
            "Megapixelsteps in past minute",
            ["type"],
        )
        self.performance_past_minute_tokens = Gauge(
            "horde_performance_past_minute_tokens", "Tokens in past minute", ["type"]
        )
        self.performance_queued_megapixelsteps = Gauge(
            "horde_performance_queued_megapixelsteps", "Queued megapixelsteps", ["type"]
        )
        self.performance_queued_tokens = Gauge(
            "horde_performance_queued_tokens", "Queued tokens", ["type"]
        )
        self.performance_queued_forms = Gauge(
            "horde_performance_queued_forms", "Queued interrogation forms", ["type"]
        )

        # Exporter health metrics
        self.scrape_success = Gauge(
            "horde_exporter_scrape_success",
            "Whether the last scrape was successful",
            ["endpoint"],
        )
        self.scrape_duration_seconds = Gauge(
            "horde_exporter_scrape_duration_seconds",
            "Duration of the last scrape",
            ["endpoint"],
        )

        # Rate-limit metrics
        self.ratelimit_limit = Gauge(
            "horde_exporter_ratelimit_limit",
            "Rate-limit ceiling reported by the API",
        )
        self.ratelimit_remaining = Gauge(
            "horde_exporter_ratelimit_remaining",
            "Requests remaining in the current rate-limit window",
        )
        self.ratelimit_reset_seconds = Gauge(
            "horde_exporter_ratelimit_reset_seconds",
            "Seconds until the rate-limit window resets",
        )
        self.ratelimit_backoff_total = Counter(
            "horde_exporter_ratelimit_backoff_total",
            "Times the exporter delayed a request due to rate-limit proximity",
        )


class HordeExporter:
    """Main exporter class"""

    def __init__(self, config: Settings):
        self.config = config
        self.metrics = HordeMetrics()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.config.api.user_agent})
        self.rate_limit = RateLimitState()

        # Track top-N entities to avoid churn
        self.top_models_image = set()
        self.top_models_text = set()
        self.top_workers_image = set()
        self.top_workers_text = set()

    def _wait_for_rate_limit(self):
        """Block if we're close to exhausting the rate-limit budget."""
        snap = self.rate_limit.snapshot()
        if snap["limit"] == 0:
            return  # no data yet

        if snap["remaining"] <= RATE_LIMIT_BACKOFF_THRESHOLD:
            wait = snap["seconds_until_reset"] + 0.5  # small buffer
            if wait > 0:
                logger.warning(
                    "Rate-limit low (%d/%d remaining), backing off %.1fs until reset",
                    snap["remaining"],
                    snap["limit"],
                    wait,
                )
                self.metrics.ratelimit_backoff_total.inc()
                time.sleep(wait)

    def _update_rate_limit_metrics(self):
        snap = self.rate_limit.snapshot()
        self.metrics.ratelimit_limit.set(snap["limit"])
        self.metrics.ratelimit_remaining.set(snap["remaining"])
        self.metrics.ratelimit_reset_seconds.set(snap["seconds_until_reset"])

    def fetch_api(self, endpoint):
        """Fetch data from AI Horde API with fallback to stablehorde.net"""
        self._wait_for_rate_limit()

        primary_url = f"{self.config.api.base_url}{endpoint}"
        fallback_url = f"https://stablehorde.net/api/v2{endpoint}"

        # Try primary URL first
        try:
            response = self.session.get(primary_url, timeout=self.config.api.timeout)
            self.rate_limit.update_from_headers(response.headers)
            self._update_rate_limit_metrics()
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Error fetching from primary URL {primary_url}: {e}")
            logger.info(f"Attempting fallback to {fallback_url}")

            # Try fallback URL
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

    def should_write_metric(self, metric_type, field_name, value):
        """
        Determine if a metric should be written to Prometheus.

        Zero values are only written if the field is NOT in the zero_omission list.
        This reduces cardinality and storage by omitting semantically null values.

        Args:
            metric_type: Type of metric ('models', 'workers', 'performance')
            field_name: Name of the field
            value: The value to potentially write

        Returns:
            bool: True if the metric should be written
        """
        # Always write non-zero values
        if value != 0 and value != 0.0:
            return True

        # Check if this field should omit zeros
        nullable_fields = getattr(self.config.zero_omission, metric_type, [])
        if field_name in nullable_fields:
            # Don't write zeros for nullable fields
            return False

        # Write zeros for non-nullable fields
        return True

    def collect_models(self, model_type="image"):
        """Collect and expose model metrics"""
        endpoint = f"/status/models?type={model_type}"
        start_time = time.time()

        try:
            data = self.fetch_api(endpoint)
            logger.info(f"Fetched {len(data)} {model_type} models")

            # Calculate aggregates
            total_queued = sum(float(m.get("queued", 0)) for m in data)
            total_count = sum(int(m.get("count", 0)) for m in data)
            total_performance = sum(float(m.get("performance", 0)) for m in data)
            total_jobs = sum(float(m.get("jobs", 0)) for m in data)
            avg_eta = sum(int(m.get("eta", 0)) for m in data) / len(data) if data else 0

            # Set aggregate metrics
            self.metrics.models_queued_total.labels(type=model_type).set(total_queued)
            self.metrics.models_workers_count.labels(type=model_type).set(total_count)
            self.metrics.models_performance.labels(type=model_type).set(
                total_performance
            )
            self.metrics.models_jobs.labels(type=model_type).set(total_jobs)
            self.metrics.models_eta_avg.labels(type=model_type).set(avg_eta)

            # Collect ALL models (no top-N filtering)
            # Zero-omission will naturally filter out inactive models
            all_models = data  # Use all models, not just top-N

            # Update tracking set (for backward compatibility, though no longer strictly needed)
            current_models = {m["name"] for m in all_models}
            if model_type == "image":
                self.top_models_image = current_models
            else:
                self.top_models_text = current_models

            # Set per-model metrics (with zero-omission for nullable fields)
            for model in all_models:
                labels = {"model": model["name"], "type": model_type}

                # Conditionally write nullable fields (queued, jobs, eta)
                queued = float(model.get("queued", 0))
                if self.should_write_metric("models", "queued", queued):
                    self.metrics.model_queued.labels(**labels).set(queued)

                jobs = float(model.get("jobs", 0))
                if self.should_write_metric("models", "jobs", jobs):
                    self.metrics.model_jobs.labels(**labels).set(jobs)

                eta = int(model.get("eta", 0))
                if self.should_write_metric("models", "eta", eta):
                    self.metrics.model_eta_seconds.labels(**labels).set(eta)

                # Always write non-nullable fields
                self.metrics.model_workers_count.labels(**labels).set(
                    int(model.get("count", 0))
                )
                self.metrics.model_performance.labels(**labels).set(
                    float(model.get("performance", 0))
                )

            logger.info(f"Collected metrics for {len(all_models)} {model_type} models")

            self.metrics.scrape_success.labels(endpoint=endpoint).set(1)
            self.metrics.scrape_duration_seconds.labels(endpoint=endpoint).set(
                time.time() - start_time
            )

        except Exception as e:
            logger.error(f"Error collecting {model_type} models: {e}")
            self.metrics.scrape_success.labels(endpoint=endpoint).set(0)

    def collect_workers(self, worker_type="image"):
        """Collect and expose worker metrics"""
        endpoint = f"/workers?type={worker_type}"
        start_time = time.time()

        try:
            data = self.fetch_api(endpoint)
            online_workers = [w for w in data if w.get("online", False)]
            logger.info(
                f"Fetched {len(online_workers)} online {worker_type} workers (out of {len(data)} total)"
            )

            # Calculate aggregates
            total_performance = sum(
                self._parse_performance(w.get("performance", "0"), worker_type)
                for w in online_workers
            )
            total_threads = sum(int(w.get("threads", 0)) for w in online_workers)
            # Note: Counter metrics need special handling - we track changes
            # For now, we'll track the current total value

            # Set aggregate metrics
            self.metrics.workers_active_total.labels(type=worker_type).set(
                len(online_workers)
            )
            self.metrics.workers_performance_total.labels(type=worker_type).set(
                total_performance
            )
            self.metrics.workers_threads_total.labels(type=worker_type).set(
                total_threads
            )

            # Collect ALL online workers (no top-N filtering)
            # Zero-omission will naturally filter out inactive workers
            all_workers = online_workers  # Use all workers, not just top-N

            # Update tracking set (for backward compatibility, though no longer strictly needed)
            current_workers = {w["name"] for w in all_workers}
            if worker_type == "image":
                self.top_workers_image = current_workers
            else:
                self.top_workers_text = current_workers

            # Set per-worker metrics (with zero-omission for nullable fields)
            for worker in all_workers:
                labels = {"worker": worker["name"], "type": worker_type}
                perf = self._parse_performance(
                    worker.get("performance", "0"), worker_type
                )

                # Always write non-nullable fields
                self.metrics.worker_kudos_rewards.labels(**labels).set(
                    float(worker.get("kudos_rewards", 0))
                )
                self.metrics.worker_performance.labels(**labels).set(perf)
                self.metrics.worker_threads.labels(**labels).set(
                    int(worker.get("threads", 0))
                )
                self.metrics.worker_requests_fulfilled.labels(**labels).set(
                    int(worker.get("requests_fulfilled", 0))
                )

                # Worker kudos details (nested fields)
                kudos_details = worker.get("kudos_details", {})
                self.metrics.worker_kudos_generated.labels(**labels).set(
                    float(kudos_details.get("generated", 0))
                )
                self.metrics.worker_kudos_uptime.labels(**labels).set(
                    float(kudos_details.get("uptime", 0))
                )

                # Worker capabilities
                self.metrics.worker_models_count.labels(**labels).set(
                    len(worker.get("models", []))
                )

                # Type-specific fields
                if worker_type == "image":
                    self.metrics.worker_max_pixels.labels(**labels).set(
                        int(worker.get("max_pixels", 0))
                    )
                    self.metrics.worker_megapixelsteps_generated.labels(**labels).set(
                        float(worker.get("megapixelsteps_generated", 0))
                    )
                elif worker_type == "text":
                    self.metrics.worker_max_length.labels(**labels).set(
                        int(worker.get("max_length", 0))
                    )
                    self.metrics.worker_max_context_length.labels(**labels).set(
                        int(worker.get("max_context_length", 0))
                    )

                # Conditionally write nullable fields (uncompleted_jobs)
                uncompleted = int(worker.get("uncompleted_jobs", 0))
                if self.should_write_metric("workers", "uncompleted_jobs", uncompleted):
                    self.metrics.worker_uncompleted_jobs.labels(**labels).set(
                        uncompleted
                    )

            logger.info(
                f"Collected metrics for {len(all_workers)} {worker_type} workers"
            )

            self.metrics.scrape_success.labels(endpoint=endpoint).set(1)
            self.metrics.scrape_duration_seconds.labels(endpoint=endpoint).set(
                time.time() - start_time
            )

        except Exception as e:
            logger.error(f"Error collecting {worker_type} workers: {e}")
            self.metrics.scrape_success.labels(endpoint=endpoint).set(0)

    def _parse_performance(self, performance_str, worker_type):
        """Parse performance string to float"""
        if isinstance(performance_str, (int, float)):
            return float(performance_str)

        try:
            if worker_type == "image":
                return float(
                    str(performance_str).replace(" megapixelsteps per second", "")
                )
            else:
                return float(str(performance_str).replace(" tokens per second", ""))
        except Exception:
            return 0.0

    def collect_performance(self):
        """Collect and expose global performance metrics"""
        endpoint = "/status/performance"
        start_time = time.time()

        try:
            data = self.fetch_api(endpoint)
            logger.info("Fetched performance metrics")

            # Conditionally write nullable queued metrics (can be zero when no activity)
            queued_requests = int(data.get("queued_requests", 0))
            if self.should_write_metric(
                "performance", "queued_requests", queued_requests
            ):
                self.metrics.performance_queued_requests.labels(type="image").set(
                    queued_requests
                )

            queued_text_requests = int(data.get("queued_text_requests", 0))
            if self.should_write_metric(
                "performance", "queued_text_requests", queued_text_requests
            ):
                self.metrics.performance_queued_requests.labels(type="text").set(
                    queued_text_requests
                )

            # Note: queued_forms had 96.7% zeros in analysis, treating as nullable
            queued_forms = int(data.get("queued_forms", 0))
            if self.should_write_metric("performance", "queued_forms", queued_forms):
                self.metrics.performance_queued_forms.labels(type="interrogator").set(
                    queued_forms
                )

            # Always write non-nullable metrics (counts, throughput, etc.)
            self.metrics.performance_worker_count.labels(type="image").set(
                int(data.get("worker_count", 0))
            )
            self.metrics.performance_worker_count.labels(type="text").set(
                int(data.get("text_worker_count", 0))
            )
            self.metrics.performance_worker_count.labels(type="interrogator").set(
                int(data.get("interrogator_count", 0))
            )
            self.metrics.performance_thread_count.labels(type="image").set(
                int(data.get("thread_count", 0))
            )
            self.metrics.performance_thread_count.labels(type="text").set(
                int(data.get("text_thread_count", 0))
            )
            self.metrics.performance_thread_count.labels(type="interrogator").set(
                int(data.get("interrogator_thread_count", 0))
            )
            self.metrics.performance_past_minute_megapixelsteps.labels(
                type="image"
            ).set(float(data.get("past_minute_megapixelsteps", 0)))
            self.metrics.performance_past_minute_tokens.labels(type="text").set(
                float(data.get("past_minute_tokens", 0))
            )
            self.metrics.performance_queued_megapixelsteps.labels(type="image").set(
                float(data.get("queued_megapixelsteps", 0))
            )
            self.metrics.performance_queued_tokens.labels(type="text").set(
                float(data.get("queued_tokens", 0))
            )

            self.metrics.scrape_success.labels(endpoint=endpoint).set(1)
            self.metrics.scrape_duration_seconds.labels(endpoint=endpoint).set(
                time.time() - start_time
            )

        except Exception as e:
            logger.error(f"Error collecting performance: {e}")
            self.metrics.scrape_success.labels(endpoint=endpoint).set(0)

    def run_models_collector(self):
        """Background thread for collecting model metrics"""
        interval = self.config.scrape_intervals.models
        while True:
            try:
                self.collect_models("image")
                self.collect_models("text")
            except Exception as e:
                logger.error(f"Error in models collector: {e}")
            time.sleep(interval)

    def run_workers_collector(self):
        """Background thread for collecting worker metrics"""
        interval = self.config.scrape_intervals.workers
        while True:
            try:
                self.collect_workers("image")
                self.collect_workers("text")
            except Exception as e:
                logger.error(f"Error in workers collector: {e}")
            time.sleep(interval)

    def run_performance_collector(self):
        """Background thread for collecting performance metrics"""
        interval = self.config.scrape_intervals.performance
        while True:
            try:
                self.collect_performance()
            except Exception as e:
                logger.error(f"Error in performance collector: {e}")
            time.sleep(interval)

    def start(self):
        """Start the exporter"""
        port = self.config.exporter.port
        logger.info(f"Starting Horde exporter on port {port}")

        # Start Prometheus HTTP server
        start_http_server(port)
        logger.info(f"Metrics available at http://localhost:{port}/metrics")

        # Start collector threads
        threading.Thread(target=self.run_models_collector, daemon=True).start()
        threading.Thread(target=self.run_workers_collector, daemon=True).start()
        threading.Thread(target=self.run_performance_collector, daemon=True).start()

        logger.info("All collectors started")

        # Keep main thread alive
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
