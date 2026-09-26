"""Consume element sets and space weather; write the nowcast to its own table.

Stateless between runs, deliberately. On start the consumer rewinds to the
beginning of every retained message and rebuilds the nowcast state from it, so
there is no checkpoint to corrupt and no offset to get wrong: the topic is the
state (ADR-0013). Offsets are therefore never committed.

A snapshot is written when the state has changed and either the snapshot
interval has passed or the consumer has just caught up -- so a restart shows a
full nowcast as soon as the replay finishes, not fifteen minutes later.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

import polars as pl
from confluent_kafka import OFFSET_BEGINNING, Consumer, Message, TopicPartition

from starlink_drag.config import Settings
from starlink_drag.stream import messages
from starlink_drag.stream.nowcast import ElementSet, NowcastState
from starlink_drag.stream.table import write_nowcast

Report = Callable[[str], None]

GENERATION_SEED: Final = Path("transform") / "seeds" / "starlink_generation_map.csv"
GROUP_ID: Final = "drag-nowcast"


def load_generations(path: Path = GENERATION_SEED) -> dict[int, str]:
    """NORAD ID -> generation, from the committed GCAT-derived seed."""
    seed = pl.read_csv(
        path, columns=["norad_id", "generation"], schema_overrides={"norad_id": pl.Int64}
    )
    return dict(zip(seed["norad_id"].to_list(), seed["generation"].to_list(), strict=True))


def apply(state: NowcastState, message: Message, settings: Settings) -> bool:
    """Feed one message to the state. False if it was a repeat, stale or unreadable."""
    value = messages.decode(message.value())
    if message.topic() == settings.stream.gp_topic:
        return state.add_element_set(ElementSet.from_message(value))
    if message.topic() == settings.stream.weather_topic:
        return state.add_observation(messages.observation_from(value))
    return False


def run(
    settings: Settings,
    *,
    generations: Mapping[int, str] | None = None,
    report: Report = print,
    stop_when_idle: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> int:
    """Consume until interrupted -- or, with ``stop_when_idle``, until nothing has
    arrived for that many seconds. Returns the number of snapshots written."""
    stream = settings.stream
    state = NowcastState(
        generations if generations is not None else load_generations(),
        window=dt.timedelta(hours=stream.window_hours),
    )
    consumer = Consumer(
        {
            "bootstrap.servers": stream.bootstrap_servers,
            "group.id": GROUP_ID,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )

    def rewind(client: Consumer, partitions: list[TopicPartition]) -> None:
        for partition in partitions:
            partition.offset = OFFSET_BEGINNING
        client.assign(partitions)

    consumer.subscribe([stream.gp_topic, stream.weather_topic], on_assign=rewind)
    snapshots = 0
    written_once = False
    last_write = clock()
    last_message = clock()
    unreadable = 0
    try:
        while True:
            batch = consumer.consume(num_messages=1000, timeout=1.0)
            for message in batch:
                if message.error():
                    report(f"consumer: {message.error()}")
                    continue
                try:
                    apply(state, message, settings)
                except (KeyError, TypeError, ValueError) as error:
                    unreadable += 1
                    report(
                        f"consumer: skipped an unreadable message ({unreadable} so far): {error}"
                    )
            if batch:
                last_message = clock()

            caught_up = not batch
            due = clock() - last_write >= stream.snapshot_seconds
            if state.dirty and (due or (caught_up and not written_once)):
                snapshot = state.snapshot(now())
                if write_nowcast(snapshot, settings):
                    snapshots += 1
                    written_once = True
                    report(f"nowcast: {_describe(snapshot)}")
                last_write = clock()

            if stop_when_idle is not None and clock() - last_message >= stop_when_idle:
                if state.dirty and write_nowcast(state.snapshot(now()), settings):
                    snapshots += 1
                return snapshots
    finally:
        consumer.close()


def _describe(snapshot: pl.DataFrame) -> str:
    if snapshot.is_empty():
        return "no satellites in the window"
    newest = snapshot["newest_epoch"][0]
    satellites = int(snapshot["satellites"].sum())
    return f"{snapshot.height} generations, {satellites:,} satellites, newest epoch {newest}"
