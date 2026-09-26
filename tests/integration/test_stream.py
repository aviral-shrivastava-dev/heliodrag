"""The streaming path end to end, through a real Redpanda broker.

Needs a broker, so it is skipped unless ``KAFKA_TEST_BOOTSTRAP`` names one:

    docker run -d -p 19092:19092 redpandadata/redpanda:v26.2.3 redpanda start \\
        --mode dev-container --smp 1 --kafka-addr 0.0.0.0:19092 \\
        --advertise-kafka-addr localhost:19092
    KAFKA_TEST_BOOTSTRAP=localhost:19092 uv run pytest tests/integration/test_stream.py

CI runs it in its own job. Every test uses its own topics and a lake in a
temporary directory. The element sets are synthetic (tests/fixtures/README.md
explains why Space-Track data cannot be committed), built on the committed
fixture's record layout; the space weather is SWPC's, captured live.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from confluent_kafka import Consumer, Message

from starlink_drag.clients import swpc
from starlink_drag.config import Settings, StreamSettings
from starlink_drag.stream import consumer, topics
from starlink_drag.stream.producer import Publisher
from starlink_drag.stream.table import latest, read_nowcast

BOOTSTRAP = os.environ.get("KAFKA_TEST_BOOTSTRAP", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not BOOTSTRAP, reason="set KAFKA_TEST_BOOTSTRAP to a Redpanda broker"),
]

T0 = dt.datetime(2026, 9, 26, 0, tzinfo=dt.UTC)
GENERATIONS = {90001: "v1.5", 90002: "v1.5", 90003: "v2-mini"}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    suffix = uuid.uuid4().hex[:8]
    stream = StreamSettings(
        bootstrap_servers=BOOTSTRAP,
        gp_topic=f"test.gp.{suffix}",
        weather_topic=f"test.weather.{suffix}",
        rejected_topic=f"test.rejected.{suffix}",
        snapshot_seconds=10,
    )
    configured = Settings(data_dir=tmp_path / "data", stream=stream)
    topics.ensure(stream)
    return configured


@pytest.fixture
def template(fixtures_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = json.loads(
        (fixtures_dir / "spacetrack" / "gp_history_sample.json").read_text(encoding="utf-8")
    )
    return rows[0]


def element_set(
    template: dict[str, Any], norad: int, hours: float, mean_motion: float
) -> dict[str, Any]:
    epoch = T0 + dt.timedelta(hours=hours)
    return {
        **template,
        "NORAD_CAT_ID": str(norad),
        "GP_ID": str(norad * 1000 + int(hours)),
        "EPOCH": epoch.replace(tzinfo=None).isoformat(),
        "MEAN_MOTION": f"{mean_motion:.8f}",
    }


def publish_round(
    publisher: Publisher, template: dict[str, Any], hours: float, mean_motion: float
) -> None:
    rows = [element_set(template, norad, hours, mean_motion) for norad in GENERATIONS]
    assert publisher.element_sets(rows).element_sets == len(rows)
    assert publisher.flush() == 0


def wait_for(condition: Callable[[], bool], timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(1.0)
    raise AssertionError("condition not met in time")


def newest(settings: Settings) -> pl.DataFrame:
    return latest(read_nowcast(settings))


def test_the_nowcast_updates_while_the_consumer_runs(
    settings: Settings, template: dict[str, Any], fixtures_dir: Path
) -> None:
    """Phase 6's acceptance: new messages change the nowcast with no restart."""
    publisher = Publisher(settings)
    weather = json.loads((fixtures_dir / "swpc" / "kyoto-dst.json").read_text(encoding="utf-8"))
    publisher.observations(*swpc.parse(swpc.DST, weather))
    publish_round(publisher, template, hours=0, mean_motion=15.06)

    running = threading.Thread(
        target=consumer.run,
        kwargs={
            "settings": settings,
            "generations": GENERATIONS,
            "report": lambda _: None,
            "stop_when_idle": 30.0,
        },
    )
    running.start()
    try:
        wait_for(lambda: not newest(settings).is_empty())
        first = newest(settings)
        assert first["rated_satellites"].sum() == 0, "one element set each: no rate yet"
        assert first["dst"].is_not_null().all()

        publish_round(publisher, template, hours=12, mean_motion=15.061)
        wait_for(lambda: newest(settings)["rated_satellites"].sum() == 3)
    finally:
        running.join(timeout=120)

    second = newest(settings)
    assert (second["computed_at"] > first["computed_at"]).all()
    assert (second["median_altitude_rate_m_per_day"] < 0).all(), "rising mean motion: falling"
    assert second["generation"].to_list() == ["v1.5", "v2-mini"]
    assert second.filter(pl.col("generation") == "v1.5")["satellites"].item() == 2


def test_a_restarted_consumer_rebuilds_the_nowcast_from_the_topic(
    settings: Settings, template: dict[str, Any]
) -> None:
    """No checkpoint: the retained messages are the state."""
    publisher = Publisher(settings)
    publish_round(publisher, template, hours=0, mean_motion=15.06)
    publish_round(publisher, template, hours=12, mean_motion=15.061)

    runs = [
        consumer.run(settings, generations=GENERATIONS, report=lambda _: None, stop_when_idle=8.0)
        for _ in range(2)
    ]

    assert all(written >= 1 for written in runs)
    snapshots = read_nowcast(settings)
    by_run = snapshots.sort("computed_at").group_by("computed_at", maintain_order=True)
    rates = [group["median_altitude_rate_m_per_day"].to_list() for _, group in by_run]
    assert rates[0] == rates[-1], "the second run replayed to the same nowcast"
    assert rates[-1][0] is not None, "the replay saw both rounds, so rates exist"


def test_an_invalid_element_set_goes_to_the_rejected_topic(
    settings: Settings, fixtures_dir: Path
) -> None:
    rows = json.loads(
        (fixtures_dir / "spacetrack" / "gp_history_sample.json").read_text(encoding="utf-8")
    )
    publisher = Publisher(settings)

    published = publisher.element_sets([rows[-1]])  # the fixture's impossible orbit
    publisher.flush()

    reader = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": f"test-{uuid.uuid4().hex[:8]}",
            "auto.offset.reset": "earliest",
        }
    )
    reader.subscribe([settings.stream.rejected_topic])
    try:
        received: list[Message] = []
        deadline = time.monotonic() + 30
        while not received and time.monotonic() < deadline:
            received = [m for m in reader.consume(num_messages=10, timeout=1.0) if not m.error()]
    finally:
        reader.close()

    assert published.rejected == 1 and published.element_sets == 0
    assert len(received) == 1
    assert json.loads(received[0].value() or b"{}")["source"] == "spacetrack"
