"""The producer's query, messages and routing -- with a stand-in for the broker.

The real round trip through Redpanda is tests/integration/test_stream.py.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, cast

import pytest
from confluent_kafka import Producer
from pydantic import ValidationError

from starlink_drag.clients.swpc import Observation, Rejection
from starlink_drag.config import Settings, StreamSettings
from starlink_drag.stream import messages
from starlink_drag.stream.producer import Publisher, gp_query


class FakeProducer:
    """Records what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    def produce(self, topic: str, *, key: bytes, value: bytes) -> None:
        self.sent.append((topic, key.decode(), messages.decode(value)))

    def poll(self, timeout: float) -> int:
        return 0

    def flush(self, timeout: float) -> int:
        return 0

    def on(self, topic: str) -> list[tuple[str, dict[str, Any]]]:
        return [(key, value) for sent_topic, key, value in self.sent if sent_topic == topic]


@pytest.fixture
def fake() -> FakeProducer:
    return FakeProducer()


@pytest.fixture
def publisher(fake: FakeProducer) -> Publisher:
    return Publisher(Settings(), producer=cast(Producer, fake))


def test_the_first_query_takes_every_current_element_set_then_only_new_ones() -> None:
    first, later = gp_query(bootstrap=True), gp_query(bootstrap=False)

    for query in (first, later):
        assert query[:2] == ("class", "gp")
        assert query[4:6] == ("OBJECT_TYPE", "PAYLOAD")
        assert query[6:8] == ("DECAY_DATE", "null-val"), "on-orbit satellites only"
    assert first[8:10] == ("EPOCH", ">now-2")
    assert later[8:10] == ("CREATION_DATE", ">now-0.125")


def test_space_track_cannot_be_polled_more_than_hourly() -> None:
    with pytest.raises(ValidationError):
        StreamSettings(gp_poll_minutes=30)


def test_valid_element_sets_are_published_keyed_by_satellite_and_invalid_ones_rejected(
    fixtures_dir: Path, fake: FakeProducer, publisher: Publisher
) -> None:
    rows = json.loads((fixtures_dir / "spacetrack" / "gp_history_sample.json").read_text())

    published = publisher.element_sets(rows)

    settings = StreamSettings()
    sent = fake.on(settings.gp_topic)
    assert published.element_sets == len(rows) - 1
    assert published.rejected == 1, "the fixture's last record is physically impossible"
    assert all(key == str(value["norad_id"]) for key, value in sent)
    assert all(value["epoch"].endswith("+00:00") for _, value in sent), "UTC stated, not implied"
    (rejected,) = fake.on(settings.rejected_topic)
    assert rejected[1]["source"] == "spacetrack"
    assert rejected[1]["reason"]


def test_space_weather_already_published_is_not_published_again(
    fake: FakeProducer, publisher: Publisher
) -> None:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    kp = Observation("kp", now, 2.0)
    dst = Observation("dst", now, -20.0)

    first = publisher.observations([kp], [])
    second = publisher.observations([kp, dst], [Rejection("products/kyoto-dst.json", {}, "bad")])

    assert (first.observations, second.observations, second.rejected) == (1, 1, 1)
    weather = fake.on(StreamSettings().weather_topic)
    assert [key for key, _ in weather] == ["kp", "dst"]
    assert messages.observation_from(weather[0][1]) == kp


def test_a_naive_timestamp_is_written_as_utc() -> None:
    value = messages.decode(
        messages.encode({"at": dt.datetime(2026, 9, 26, 12), "day": dt.date(2026, 9, 26)})
    )

    assert value == {"at": "2026-09-26T12:00:00+00:00", "day": "2026-09-26"}


def test_a_message_must_be_a_json_object() -> None:
    with pytest.raises(ValueError):
        messages.decode(b"[1, 2]")
    with pytest.raises(ValueError):
        messages.decode(None)
