"""NOAA Space Weather Prediction Center: space weather as it happens.

The streaming path's forcing (ADR-0014). OMNI, which the batch path uses, is
the better record but arrives about a week late; SWPC publishes within minutes.
Public, no account, US-government work in the public domain.

Four products, each a JSON array of records with a UTC ``time_tag``:

=====================================  =============  =====================
Product                                Quantity       Cadence
=====================================  =============  =====================
``json/planetary_k_index_1m.json``     kp_estimated   1 minute, provisional
``products/noaa-planetary-k-index.json``  kp, ap      3 hours
``products/kyoto-dst.json``            dst            1 hour, quick-look
``products/10cm-flux-30-day.json``     f107           daily, at 20:00 UTC
=====================================  =============  =====================

Parsing is separate from fetching and pure, so it is tested on captured
responses. A value outside what the quantity can physically be is rejected
with a reason rather than passed on: SWPC's quick-look products are revised,
and a bad provisional value must not become a nowcast.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx

Quantity = Literal["kp_estimated", "kp", "ap", "dst", "f107"]


@dataclass(frozen=True, slots=True)
class Product:
    path: str
    fields: tuple[tuple[str, Quantity], ...]
    """(JSON field, quantity) pairs read from each record."""


KP_1M: Final = Product("json/planetary_k_index_1m.json", (("estimated_kp", "kp_estimated"),))
KP_3H: Final = Product("products/noaa-planetary-k-index.json", (("Kp", "kp"), ("a_running", "ap")))
DST: Final = Product("products/kyoto-dst.json", (("dst", "dst"),))
F107: Final = Product("products/10cm-flux-30-day.json", (("flux", "f107"),))
PRODUCTS: Final = (KP_1M, KP_3H, DST, F107)

#: What each quantity can physically be. Kp is 0-9 by definition and ap its
#: linear equivalent (at most 400). Dst reached about -1,760 nT in 1859; OMNI
#: marks F10.7 fills as 999.9, so anything that high is a fill, not a flux.
BOUNDS: Final[dict[Quantity, tuple[float, float]]] = {
    "kp_estimated": (0.0, 9.0),
    "kp": (0.0, 9.0),
    "ap": (0.0, 400.0),
    "dst": (-2000.0, 500.0),
    "f107": (30.0, 900.0),
}


@dataclass(frozen=True, slots=True)
class Observation:
    quantity: Quantity
    observed_at: dt.datetime
    """Timezone-aware UTC."""
    value: float

    @property
    def key(self) -> str:
        """Identifies the observation: a re-published one has the same key."""
        return f"{self.quantity}@{self.observed_at.isoformat()}"


@dataclass(frozen=True, slots=True)
class Rejection:
    product: str
    record: dict[str, Any]
    reason: str


def parse(product: Product, payload: object) -> tuple[list[Observation], list[Rejection]]:
    """Every observation in one product's response, and every record refused."""
    if not isinstance(payload, list):
        return [], [Rejection(product.path, {}, "expected a JSON array")]

    observations: list[Observation] = []
    rejections: list[Rejection] = []
    for record in payload:
        if not isinstance(record, dict):
            rejections.append(Rejection(product.path, {}, "record is not an object"))
            continue
        try:
            observed_at = _utc(record["time_tag"])
        except (KeyError, TypeError, ValueError) as error:
            rejections.append(Rejection(product.path, record, f"bad time_tag: {error}"))
            continue
        for field, quantity in product.fields:
            if field not in record:
                # A renamed field would otherwise produce no values and no
                # error -- schema drift, silently. Absent is not the same as null.
                rejections.append(Rejection(product.path, record, f"no {field} field"))
                continue
            value = record[field]
            if value is None:
                continue  # an explicit null: no value for this period, not an error
            try:
                number = float(value)
            except (TypeError, ValueError):
                rejections.append(Rejection(product.path, record, f"{field} is not a number"))
                continue
            low, high = BOUNDS[quantity]
            if not low <= number <= high:
                rejections.append(
                    Rejection(product.path, record, f"{quantity} {number} outside [{low}, {high}]")
                )
                continue
            observations.append(Observation(quantity, observed_at, number))
    return observations, rejections


def fetch(
    base_url: str, product: Product, *, client: httpx.Client | None = None
) -> tuple[list[Observation], list[Rejection]]:
    """Fetch and parse one product. Raises on HTTP failure."""
    url = f"{base_url.rstrip('/')}/{product.path}"
    owned = client is None
    http = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        response = http.get(url)
        response.raise_for_status()
        return parse(product, response.json())
    finally:
        if owned:
            http.close()


def _utc(time_tag: object) -> dt.datetime:
    """SWPC time tags are UTC with no zone marker; make that explicit."""
    if not isinstance(time_tag, str):
        raise TypeError("time_tag is not a string")
    parsed = dt.datetime.fromisoformat(time_tag.removesuffix("Z"))
    return parsed.replace(tzinfo=dt.UTC) if parsed.tzinfo is None else parsed.astimezone(dt.UTC)
