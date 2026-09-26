"""Creating the topics, with retention chosen for what each one is for.

Retention is the consumer's memory. It keeps no state of its own: on start it
replays every retained message, so a week of element sets means a restarted
consumer is back to a full nowcast in seconds rather than after a day of
waiting for each satellite to report twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

from starlink_drag.config import StreamSettings

DAY_MS: Final = 24 * 60 * 60 * 1000


@dataclass(frozen=True, slots=True)
class TopicSpec:
    name: str
    retention_days: int
    partitions: int = 1


def specs(settings: StreamSettings) -> tuple[TopicSpec, ...]:
    return (
        TopicSpec(settings.gp_topic, retention_days=7),
        TopicSpec(settings.weather_topic, retention_days=7),
        # Refused records are kept longer: they are what a person reads when
        # something upstream has changed shape.
        TopicSpec(settings.rejected_topic, retention_days=30),
    )


def ensure(settings: StreamSettings, *, timeout: float = 30.0) -> list[str]:
    """Create any missing topic. Returns the names created."""
    admin = AdminClient({"bootstrap.servers": settings.bootstrap_servers})
    existing = set(admin.list_topics(timeout=timeout).topics)
    wanted = [spec for spec in specs(settings) if spec.name not in existing]
    if not wanted:
        return []
    futures = admin.create_topics(
        [
            NewTopic(
                spec.name,
                num_partitions=spec.partitions,
                replication_factor=1,
                config={"retention.ms": str(spec.retention_days * DAY_MS)},
            )
            for spec in wanted
        ]
    )
    for future in futures.values():
        future.result(timeout=timeout)
    return [spec.name for spec in wanted]
