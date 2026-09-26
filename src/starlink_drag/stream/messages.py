"""What travels on the topics, and how it is keyed. Pure: no Kafka here.

JSON, because every value is small and a person reading a topic with ``rpk
topic consume`` should be able to see what is in it.

Keys decide ordering. Kafka orders messages within a partition, and a key
always maps to the same partition, so keying element sets by NORAD ID keeps
each satellite's element sets in the order they were published -- which is the
order the nowcast needs to difference them. Space weather is keyed by quantity
for the same reason.

Every timestamp is written with an explicit UTC offset. Bronze's datetimes are
naive UTC by convention; a naive one reaching a consumer in another time zone
would be read as local time (see the runbook, "Timestamps are UTC instants").
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator, Mapping
from typing import Any

import polars as pl

from starlink_drag.clients.swpc import Observation, Quantity, Rejection


def encode(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, default=_json_default, separators=(",", ":")).encode("utf-8")


def decode(raw: bytes | str | None) -> dict[str, Any]:
    if raw is None:
        raise ValueError("message has no value")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("message value is not a JSON object")
    return value


def element_sets(frame: pl.DataFrame) -> Iterator[tuple[str, dict[str, Any]]]:
    """(key, value) for each validated element set: the bronze row, keyed by NORAD ID."""
    for row in frame.iter_rows(named=True):
        yield str(row["norad_id"]), row


def observation(item: Observation) -> tuple[str, dict[str, Any]]:
    return item.quantity, {
        "quantity": item.quantity,
        "observed_at": item.observed_at,
        "value": item.value,
    }


def observation_from(value: Mapping[str, Any]) -> Observation:
    quantity: Quantity = value["quantity"]
    return Observation(quantity, _aware(str(value["observed_at"])), float(value["value"]))


def rejected_element_set(row: Mapping[str, Any], reason: str) -> tuple[str, dict[str, Any]]:
    return "spacetrack", {"source": "spacetrack", "reason": reason, "record": dict(row)}


def rejected_observation(item: Rejection) -> tuple[str, dict[str, Any]]:
    return "swpc", {"source": f"swpc:{item.product}", "reason": item.reason, "record": item.record}


def _aware(text: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(text)
    return parsed.replace(tzinfo=dt.UTC) if parsed.tzinfo is None else parsed


def _json_default(value: object) -> str:
    if isinstance(value, dt.datetime):
        return (value if value.tzinfo else value.replace(tzinfo=dt.UTC)).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")
