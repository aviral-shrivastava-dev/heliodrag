"""Publish current Starlink element sets and SWPC space weather to Redpanda.

One loop, two schedules. Space-Track is asked at most once an hour -- its own
guidance for current element sets, enforced by the settings' floor -- and every
request goes through the same client and shared rate-limit ledger as the batch
path (ADR-0008). SWPC is asked every few minutes.

Element sets are validated against the bronze contract before they are
published; a record that fails goes to the rejected topic with its reason, as
batch quarantine does, rather than into the nowcast. The first Space-Track
query takes every Starlink's latest element set from the last two days, so
the nowcast has a BSTAR for every satellite at once; later ones take what
Space-Track has published in the last three hours. The overlap is deliberate
-- a late poll loses nothing -- and the consumer drops repeats.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx
from confluent_kafka import Producer

from starlink_drag.clients import swpc
from starlink_drag.clients.spacetrack import SpaceTrackClient, SpaceTrackError
from starlink_drag.config import Settings
from starlink_drag.schemas import gp
from starlink_drag.schemas.validate import validate
from starlink_drag.stream import messages, topics

Report = Callable[[str], None]

BOOTSTRAP_WINDOW: Final = (">now-2",)
"""Latest element set of every Starlink with an epoch in the last two days."""
UPDATE_WINDOW: Final = (">now-0.125",)
"""Element sets Space-Track created in the last three hours."""
SEEN_RETENTION: Final = dt.timedelta(days=8)


def gp_query(*, bootstrap: bool) -> tuple[str, ...]:
    """Segments of the Space-Track query: on-orbit Starlink payloads, current elements."""
    field, window = ("EPOCH", BOOTSTRAP_WINDOW) if bootstrap else ("CREATION_DATE", UPDATE_WINDOW)
    return (
        "class", "gp",
        "OBJECT_NAME", "~~STARLINK",
        "OBJECT_TYPE", "PAYLOAD",
        "DECAY_DATE", "null-val",
        field, *window,
        "orderby", "NORAD_CAT_ID",
    )  # fmt: skip


@dataclass
class Published:
    element_sets: int = 0
    observations: int = 0
    rejected: int = 0

    def describe(self) -> str:
        counts = []
        if self.element_sets or not self.observations:
            counts.append(f"{self.element_sets:,} element sets")
        if self.observations or not self.element_sets:
            counts.append(f"{self.observations:,} new space-weather values")
        return f"published {', '.join(counts)}, {self.rejected:,} rejected"


class Publisher:
    """Wraps the Kafka producer with the topics and the repeat filter."""

    def __init__(self, settings: Settings, producer: Producer | None = None) -> None:
        self._stream = settings.stream
        self._producer = producer or Producer(
            {
                "bootstrap.servers": self._stream.bootstrap_servers,
                # Exactly one copy of each message per send, even across retries.
                "enable.idempotence": True,
                "acks": "all",
                "linger.ms": 100,
            }
        )
        self._seen: dict[str, dt.datetime] = {}

    def element_sets(self, rows: list[dict[str, Any]]) -> Published:
        outcome = validate(gp.to_frame(rows), gp.schema, source="spacetrack_gp_stream")
        published = Published()
        for key, value in messages.element_sets(outcome.valid):
            self._send(self._stream.gp_topic, key, value)
            published.element_sets += 1
        for row in outcome.quarantined.iter_rows(named=True):
            key, value = messages.rejected_element_set(row, str(row["failure_reason"]))
            self._send(self._stream.rejected_topic, key, value)
            published.rejected += 1
        return published

    def observations(
        self, observations: list[swpc.Observation], rejections: list[swpc.Rejection]
    ) -> Published:
        """Publish values not already published. SWPC re-serves hours of history."""
        published = Published()
        for item in observations:
            if item.key in self._seen:
                continue
            self._seen[item.key] = item.observed_at
            key, value = messages.observation(item)
            self._send(self._stream.weather_topic, key, value)
            published.observations += 1
        for rejection in rejections:
            key, value = messages.rejected_observation(rejection)
            self._send(self._stream.rejected_topic, key, value)
            published.rejected += 1
        horizon = dt.datetime.now(dt.UTC) - SEEN_RETENTION
        self._seen = {k: t for k, t in self._seen.items() if t >= horizon}
        return published

    def flush(self, timeout: float = 30.0) -> int:
        """Wait for delivery. Returns the messages still undelivered."""
        return int(self._producer.flush(timeout))

    def _send(self, topic: str, key: str, value: Mapping[str, Any]) -> None:
        self._producer.produce(topic, key=key.encode("utf-8"), value=messages.encode(value))
        self._producer.poll(0)  # serve delivery callbacks so the queue drains


def run(
    settings: Settings,
    *,
    once: bool = False,
    report: Report = print,
    publisher: Publisher | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Poll and publish until interrupted, or for one cycle when ``once``."""
    stream = settings.stream
    created = topics.ensure(stream)
    if created:
        report(f"created topics: {', '.join(created)}")
    out = publisher or Publisher(settings)
    has_spacetrack = settings.spacetrack.is_configured
    if not has_spacetrack:
        report("no Space-Track credentials: publishing space weather only")

    with SpaceTrackClient(settings.spacetrack) as spacetrack, httpx.Client(timeout=30.0) as http:
        bootstrap = True
        next_gp = next_weather = time.monotonic()
        while True:
            now = time.monotonic()
            if has_spacetrack and now >= next_gp:
                try:
                    rows = spacetrack.query(*gp_query(bootstrap=bootstrap))
                    report(f"space-track: {out.element_sets(rows).describe()}")
                    bootstrap = False
                except (SpaceTrackError, httpx.HTTPError) as error:
                    report(f"space-track: FAILED, retrying next poll: {error}")
                next_gp = now + stream.gp_poll_minutes * 60
            if now >= next_weather:
                total = Published()
                for product in swpc.PRODUCTS:
                    try:
                        batch = out.observations(
                            *swpc.fetch(stream.swpc_base_url, product, client=http)
                        )
                    except (httpx.HTTPError, ValueError) as error:
                        report(f"swpc {product.path}: FAILED, retrying next poll: {error}")
                        continue
                    total.observations += batch.observations
                    total.rejected += batch.rejected
                report(f"swpc: {total.describe()}")
                next_weather = now + stream.swpc_poll_minutes * 60
            undelivered = out.flush()
            if undelivered:
                report(f"WARNING: {undelivered} message(s) not yet delivered to Redpanda")
            if once:
                return
            wake = min(next_gp if has_spacetrack else next_weather, next_weather)
            sleep(max(1.0, wake - time.monotonic()))
