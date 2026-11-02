"""A series stops being exported once the API no longer reports it.

prometheus_client keeps a labelled series until it is removed, so each collector pass
removes the series its previous run wrote that this run did not.
"""

from collections.abc import Callable

import pytest
from prometheus_client import REGISTRY

from ai_horde_stats_exporter.config import Settings
from ai_horde_stats_exporter.exporter import HordeExporter
from ai_horde_stats_exporter.specs import HordeType

TEXT_MODELS = "/status/models?type=text"
IMAGE_MODELS = "/status/models?type=image"
TEXT_WORKERS = "/workers?type=text"
PERFORMANCE = "/status/performance"


@pytest.fixture(scope="module")
def exporter() -> HordeExporter:
    # HordeMetrics registers its gauges on the default registry, which allows one
    # instance per process; the tests share it and use names no other test uses.
    return HordeExporter(Settings())


def serve(exporter: HordeExporter, responses: dict[str, object]) -> None:
    """Make the exporter's API fetches return `responses`, keyed by endpoint."""

    def fetch_api(endpoint: str) -> object:
        return responses[endpoint]

    exporter.fetch_api = fetch_api  # type: ignore[method-assign]


def failing_fetch(endpoint: str) -> object:
    raise RuntimeError(f"API unreachable for {endpoint}")


def value(name: str, **labels: str) -> float | None:
    return REGISTRY.get_sample_value(name, labels)


def collect_text_models(exporter: HordeExporter, models: list[dict]) -> None:
    serve(exporter, {TEXT_MODELS: models})
    exporter.collect_models(HordeType.TEXT)


def test_model_missing_from_the_response_is_removed(exporter: HordeExporter):
    collect_text_models(
        exporter,
        [{"name": "lifecycle/a", "count": 2}, {"name": "lifecycle/b", "count": 1}],
    )
    assert value("horde_model_workers_count", model="lifecycle/b", type="text") == 1

    collect_text_models(exporter, [{"name": "lifecycle/a", "count": 2}])

    assert value("horde_model_workers_count", model="lifecycle/a", type="text") == 2
    assert value("horde_model_workers_count", model="lifecycle/b", type="text") is None
    assert value("horde_model_performance", model="lifecycle/b", type="text") is None


def test_zero_omitted_field_is_removed_when_it_drops_to_zero(exporter: HordeExporter):
    collect_text_models(exporter, [{"name": "queue/a", "count": 1, "queued": 5}])
    assert value("horde_model_queued", model="queue/a", type="text") == 5

    collect_text_models(exporter, [{"name": "queue/a", "count": 1, "queued": 0}])

    assert value("horde_model_queued", model="queue/a", type="text") is None
    assert value("horde_model_workers_count", model="queue/a", type="text") == 1


def test_failed_fetch_keeps_the_last_values(exporter: HordeExporter):
    collect_text_models(exporter, [{"name": "kept/a", "count": 3}])

    exporter.fetch_api = failing_fetch  # type: ignore[method-assign]
    exporter.collect_models(HordeType.TEXT)

    assert value("horde_model_workers_count", model="kept/a", type="text") == 3
    assert value("horde_exporter_scrape_success", endpoint=TEXT_MODELS) == 0


def test_text_pass_leaves_image_series_alone(exporter: HordeExporter):
    serve(exporter, {IMAGE_MODELS: [{"name": "image/a", "count": 4}]})
    exporter.collect_models(HordeType.IMAGE)

    collect_text_models(exporter, [{"name": "text/a", "count": 1}])
    collect_text_models(exporter, [{"name": "text/b", "count": 1}])

    assert value("horde_model_workers_count", model="image/a", type="image") == 4


def collect_text_workers(exporter: HordeExporter, workers: list[dict]) -> None:
    serve(exporter, {TEXT_WORKERS: workers})
    exporter.collect_workers(HordeType.TEXT)


@pytest.mark.parametrize(
    "second_pass",
    [
        pytest.param(lambda w: {**w, "online": False}, id="offline"),
        pytest.param(lambda w: None, id="unlisted"),
    ],
)
def test_worker_no_longer_online_is_removed(
    exporter: HordeExporter, second_pass: Callable[[dict], dict | None]
):
    kept = {"name": "worker/kept", "online": True, "threads": 1}
    gone = {"name": f"worker/gone-{id(second_pass)}", "online": True, "threads": 2}
    collect_text_workers(exporter, [kept, gone])
    assert value("horde_worker_threads", worker=gone["name"], type="text") == 2

    collect_text_workers(exporter, [kept] + [w for w in [second_pass(gone)] if w])

    assert value("horde_worker_threads", worker=gone["name"], type="text") is None
    assert (
        value(
            "horde_worker_info",
            worker=gone["name"],
            type="text",
            bridge_agent="unknown",
        )
        is None
    )
    assert value("horde_worker_threads", worker="worker/kept", type="text") == 1


def test_bridge_agent_change_replaces_the_info_series(exporter: HordeExporter):
    worker = {"name": "worker/upgrading", "online": True}
    collect_text_workers(exporter, [{**worker, "bridge_agent": "bridge:1"}])
    collect_text_workers(exporter, [{**worker, "bridge_agent": "bridge:2"}])

    labels = {"worker": "worker/upgrading", "type": "text"}
    assert value("horde_worker_info", **labels, bridge_agent="bridge:1") is None
    assert value("horde_worker_info", **labels, bridge_agent="bridge:2") == 1


def test_performance_queue_is_removed_when_it_empties(exporter: HordeExporter):
    serve(exporter, {PERFORMANCE: {"queued_text_requests": 10, "text_worker_count": 3}})
    exporter.collect_performance()
    assert value("horde_performance_queued_requests", type="text") == 10

    serve(exporter, {PERFORMANCE: {"queued_text_requests": 0, "text_worker_count": 3}})
    exporter.collect_performance()

    assert value("horde_performance_queued_requests", type="text") is None
    assert value("horde_performance_worker_count", type="text") == 3
