"""Main exporter class — API fetching, metric emission, collectors, and thread runners."""

import logging
import threading
import time
from collections.abc import Sequence
from typing import Any

import requests
from prometheus_client import start_http_server

from .config import RATE_LIMIT_BACKOFF_THRESHOLD, Settings
from .metrics import HordeMetrics
from .models import (
    ApiModel,
    HordeModelStatus,
    HordeTeam,
    HordeWorker,
    ModesResponse,
    PerformanceStatus,
    StatsModelsResponse,
)
from .rate_limit import RateLimitState
from .specs import (
    MODEL_AGGREGATES,
    MODEL_FIELDS,
    MODES_FIELDS,
    PERFORMANCE_FIELDS,
    PERFORMANCE_SYNTHESIZED,
    STATS_MODELS,
    STATS_TOTALS,
    TEAM_FIELDS,
    WORKER_AGGREGATES,
    WORKER_FIELDS,
    AggregateFunc,
    AggregateSpec,
    FieldSpec,
    HordeType,
    StatsTotalsSpec,
    StatsModelSpec,
)

logger = logging.getLogger(__name__)


class HordeExporter:
    """Main exporter class."""

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
        """Fetch data from AI Horde API with fallback to stablehorde.net."""
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
        entity_type: HordeType | None = None,
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

            g = self.metrics.gauge(spec.metric_name)
            if labels:
                g.labels(**labels).set(value)
            else:
                g.set(value)

    def _emit_aggregates(
        self,
        specs: list[AggregateSpec],
        entities: Sequence[ApiModel],
        type_label: HordeType,
    ):
        """Compute and emit aggregate metrics across a list of entities."""
        for spec in specs:
            if spec.condition and spec.condition != type_label:
                continue

            match spec.func:
                case AggregateFunc.COUNT:
                    value = float(len(entities))
                case AggregateFunc.SUM:
                    value = sum(
                        float(self._resolve_attr(e, spec.attr)) for e in entities
                    )
                case AggregateFunc.MEAN:
                    value = (
                        sum(float(self._resolve_attr(e, spec.attr)) for e in entities)
                        / len(entities)
                        if entities
                        else 0.0
                    )
                case AggregateFunc.COUNT_TRUE:
                    value = float(
                        sum(1 for e in entities if self._resolve_attr(e, spec.attr))
                    )

            self.metrics.gauge(spec.metric_name).labels(type=type_label).set(value)

    def _emit_performance(self, perf: PerformanceStatus):
        """Emit all performance metrics from spec tables."""
        for spec in PERFORMANCE_FIELDS:
            value = float(getattr(perf, spec.attr))
            if spec.zero_omit_key and not self.should_write_metric(
                "performance", spec.zero_omit_key, value
            ):
                continue
            self.metrics.gauge(spec.metric_name).labels(type=spec.type_label).set(value)

        for spec in PERFORMANCE_SYNTHESIZED:
            numerator = float(getattr(perf, spec.numerator_attr))
            denominator = float(getattr(perf, spec.denominator_attr))
            if denominator > 0:
                value = numerator / denominator * spec.multiplier
                self.metrics.gauge(spec.metric_name).labels(type=spec.type_label).set(
                    value
                )

    def _emit_stats_totals(self, totals_spec: StatsTotalsSpec):
        """Emit period-based stats for a single totals endpoint."""
        endpoint = totals_spec.endpoint
        start_time = time.time()
        try:
            stats = totals_spec.response_type(**self.fetch_api(endpoint))
            for period_name in totals_spec.periods:
                period = getattr(stats, period_name)
                for field_spec in totals_spec.fields:
                    value = float(getattr(period, field_spec.period_attr))
                    self.metrics.gauge(field_spec.metric_name).labels(
                        period=period_name
                    ).set(value)
            self._record_scrape_success(endpoint, start_time)
        except Exception as e:
            logger.error(f"Error collecting stats totals ({endpoint}): {e}")
            self._record_scrape_failure(endpoint)

    def _emit_stats_models(self, model_spec: StatsModelSpec):
        """Emit per-model stats for a single models endpoint."""
        endpoint = model_spec.endpoint
        start_time = time.time()
        try:
            stats = StatsModelsResponse(**self.fetch_api(endpoint))
            for period_name in model_spec.periods:
                for model_name, count in getattr(stats, period_name).items():
                    self.metrics.gauge(model_spec.metric_name).labels(
                        model=model_name, period=period_name
                    ).set(count)
            self._record_scrape_success(endpoint, start_time)
        except Exception as e:
            logger.error(f"Error collecting stats models ({endpoint}): {e}")
            self._record_scrape_failure(endpoint)

    # --- collectors ---

    def collect_models(self, model_type: HordeType):
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

    def collect_workers(self, worker_type: HordeType):
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
            self._emit_performance(perf)
            self._record_scrape_success(endpoint, start_time)

        except Exception as e:
            logger.error(f"Error collecting performance: {e}")
            self._record_scrape_failure(endpoint)

    def collect_stats_totals(self):
        for totals_spec in STATS_TOTALS:
            self._emit_stats_totals(totals_spec)

    def collect_stats_models(self):
        for model_spec in STATS_MODELS:
            self._emit_stats_models(model_spec)

    def collect_modes(self):
        # Heartbeat
        hb_endpoint = "/status/heartbeat"
        try:
            self.fetch_api(hb_endpoint)
            self.metrics.gauge("horde_api_up").set(1)
            self.metrics.gauge("horde_exporter_scrape_success").labels(
                endpoint=hb_endpoint
            ).set(1)
        except Exception:
            self.metrics.gauge("horde_api_up").set(0)
            self.metrics.gauge("horde_exporter_scrape_success").labels(
                endpoint=hb_endpoint
            ).set(0)

        # Modes
        modes_endpoint = "/status/modes"
        start_time = time.time()
        try:
            modes = ModesResponse(**self.fetch_api(modes_endpoint))
            self._emit_fields(MODES_FIELDS, modes, {}, "modes")
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
                self.collect_models(HordeType.IMAGE)
                self.collect_models(HordeType.TEXT)
            except Exception as e:
                logger.error(f"Error in models collector: {e}")
            time.sleep(interval)

    def run_workers_collector(self):
        interval = self.config.scrape_intervals.workers
        while True:
            try:
                self.collect_workers(HordeType.IMAGE)
                self.collect_workers(HordeType.TEXT)
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
        """Start the exporter."""
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
