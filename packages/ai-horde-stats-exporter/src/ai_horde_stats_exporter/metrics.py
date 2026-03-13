"""Prometheus metric registry for the AI Horde Stats Exporter."""

from prometheus_client import Counter, Gauge

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
)


class HordeMetrics:
    """Creates and indexes every Prometheus metric the exporter produces.

    Per-entity and aggregate metrics are registered from the spec tables.
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

        # Performance metrics from spec tables (deduplicate shared metric names)
        _perf_registered: set[str] = set()
        for spec in PERFORMANCE_FIELDS:
            if spec.metric_name not in _perf_registered:
                self._add_gauge(spec.metric_name, spec.help_text, ["type"])
                _perf_registered.add(spec.metric_name)
        for spec in PERFORMANCE_SYNTHESIZED:
            if spec.metric_name not in _perf_registered:
                self._add_gauge(spec.metric_name, spec.help_text, ["type"])
                _perf_registered.add(spec.metric_name)

        # Stats metrics from spec tables
        for totals_spec in STATS_TOTALS:
            for field_spec in totals_spec.fields:
                self._add_gauge(
                    field_spec.metric_name, field_spec.help_text, ["period"]
                )
        for model_spec in STATS_MODELS:
            self._add_gauge(
                model_spec.metric_name, model_spec.help_text, ["model", "period"]
            )

        # Mode fields from spec table
        for spec in MODES_FIELDS:
            self._add_gauge(spec.metric_name, spec.help_text)

        # Heartbeat
        self._add_gauge("horde_api_up", "Whether the AI Horde API is reachable")

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
