"""The drag nowcast: a rolling, per-generation summary of the last day. Pure.

No Kafka and no I/O. The consumer feeds this element sets and space-weather
observations in whatever order the topics deliver them; this keeps the state
and turns it into one row per generation on demand. Replaying the same
messages gives the same state, which is what lets the consumer rebuild itself
from the topic after a restart.

Two measures per generation, because they answer on different timescales:

- **BSTAR**, Space-Track's fitted drag term, is on every element set. It is a
  drag signal from the first message, but a fitted one: it absorbs model error
  as well as air density.
- **Altitude rate** is the change in orbit between a satellite's consecutive
  element sets, the same quantity ``fct_daily_decay`` carries. It needs two
  element sets per satellite, so it fills in over the first hours.

Each satellite counts once per generation, with its newest value, so a
satellite updated four times a day does not outweigh one updated twice.

The window is **event time**: the last ``window`` of element-set epochs, up to
the newest epoch seen, not the wall clock. A replay therefore computes the same
nowcast it did live, and a stalled feed shows as a stale newest epoch rather
than as an empty window.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import polars as pl

from starlink_drag.clients.swpc import Observation, Quantity
from starlink_drag.science import orbital
from starlink_drag.science.generations import UNKNOWN

#: A mean-motion rate below this is a thruster raising the orbit, not drag.
#: The same threshold as ``is_likely_manoeuvring`` in int_decay__daily_rates.sql;
#: a test holds the two together.
MANOEUVRE_MEAN_MOTION_RATE: Final = -0.0005

#: Element sets closer than this are too close for a difference-based rate to
#: rise above fitting noise; further apart and a manoeuvre can hide inside.
MIN_INTERVAL: Final = dt.timedelta(hours=6)
MAX_INTERVAL: Final = dt.timedelta(days=3)

WEATHER: Final[tuple[Quantity, ...]] = ("kp_estimated", "kp", "ap", "dst", "f107")

NOWCAST_SCHEMA: Final[dict[str, pl.DataType | type[pl.DataType]]] = {
    "computed_at": pl.Datetime("us", "UTC"),
    "computed_date": pl.Date,
    "generation": pl.Utf8,
    "window_hours": pl.Int32,
    "window_start": pl.Datetime("us", "UTC"),
    "newest_epoch": pl.Datetime("us", "UTC"),
    "satellites": pl.Int32,
    "rated_satellites": pl.Int32,
    "manoeuvring_satellites": pl.Int32,
    "median_bstar": pl.Float64,
    "median_altitude_rate_m_per_day": pl.Float64,
    "p25_altitude_rate_m_per_day": pl.Float64,
    "p75_altitude_rate_m_per_day": pl.Float64,
    **{f"{q}": pl.Float64 for q in WEATHER},
    **{f"{q}_observed_at": pl.Datetime("us", "UTC") for q in WEATHER},
}


@dataclass(frozen=True, slots=True)
class ElementSet:
    norad_id: int
    gp_id: int
    epoch: dt.datetime
    """Timezone-aware UTC."""
    mean_motion: float
    bstar: float | None

    @classmethod
    def from_message(cls, value: Mapping[str, Any]) -> ElementSet:
        epoch = dt.datetime.fromisoformat(str(value["epoch"]))
        bstar = value.get("bstar")
        return cls(
            norad_id=int(value["norad_id"]),
            gp_id=int(value["gp_id"]),
            epoch=epoch.replace(tzinfo=dt.UTC) if epoch.tzinfo is None else epoch,
            mean_motion=float(value["mean_motion"]),
            bstar=None if bstar is None else float(bstar),
        )


@dataclass(frozen=True, slots=True)
class Sample:
    """One element set, with its rate when a usable predecessor exists."""

    norad_id: int
    epoch: dt.datetime
    bstar: float | None
    altitude_rate_km_per_day: float | None
    is_manoeuvring: bool


@dataclass
class NowcastState:
    generations: Mapping[int, str]
    window: dt.timedelta = dt.timedelta(hours=24)
    newest_epoch: dt.datetime | None = None
    dirty: bool = False
    _history: dict[int, list[ElementSet]] = field(default_factory=dict)
    _samples: dict[int, Sample] = field(default_factory=dict)
    _weather: dict[Quantity, Observation] = field(default_factory=dict)

    def add_element_set(self, element_set: ElementSet) -> bool:
        """Take one element set. False if it is a repeat or older than one held."""
        history = self._history.setdefault(element_set.norad_id, [])
        if history and element_set.epoch <= history[-1].epoch:
            return False
        # The rate is measured against the newest element set far enough back
        # to rise above fitting noise, not simply the previous one: Space-Track
        # sometimes publishes two within an hour.
        base = next(
            (
                e
                for e in reversed(history)
                if MIN_INTERVAL <= element_set.epoch - e.epoch <= MAX_INTERVAL
            ),
            None,
        )
        self._samples[element_set.norad_id] = _sample(element_set, base)
        history.append(element_set)
        history[:] = [e for e in history if element_set.epoch - e.epoch <= MAX_INTERVAL]
        if self.newest_epoch is None or element_set.epoch > self.newest_epoch:
            self.newest_epoch = element_set.epoch
        self.dirty = True
        return True

    def add_observation(self, observation: Observation) -> bool:
        """Take one space-weather value. False unless it is newer than the one held."""
        held = self._weather.get(observation.quantity)
        if held is not None and observation.observed_at <= held.observed_at:
            return False
        self._weather[observation.quantity] = observation
        self.dirty = True
        return True

    def snapshot(self, computed_at: dt.datetime) -> pl.DataFrame:
        """One row per generation with a satellite in the window."""
        self.dirty = False
        if self.newest_epoch is None:
            return pl.DataFrame(schema=NOWCAST_SCHEMA)
        start = self.newest_epoch - self.window
        recent = [s for s in self._samples.values() if s.epoch >= start]
        if not recent:
            return pl.DataFrame(schema=NOWCAST_SCHEMA)

        samples = pl.DataFrame(
            {
                "generation": [self.generations.get(s.norad_id, UNKNOWN) for s in recent],
                "bstar": [s.bstar for s in recent],
                "rate_m": [
                    None
                    if s.altitude_rate_km_per_day is None or s.is_manoeuvring
                    else s.altitude_rate_km_per_day * 1000.0
                    for s in recent
                ],
                "rated": [s.altitude_rate_km_per_day is not None for s in recent],
                "manoeuvring": [s.is_manoeuvring for s in recent],
            },
            schema_overrides={"bstar": pl.Float64, "rate_m": pl.Float64},
        )
        summary = samples.group_by("generation").agg(
            pl.len().cast(pl.Int32).alias("satellites"),
            pl.col("rated").sum().cast(pl.Int32).alias("rated_satellites"),
            pl.col("manoeuvring").sum().cast(pl.Int32).alias("manoeuvring_satellites"),
            pl.col("bstar").median().alias("median_bstar"),
            pl.col("rate_m").median().alias("median_altitude_rate_m_per_day"),
            pl.col("rate_m").quantile(0.25, "linear").alias("p25_altitude_rate_m_per_day"),
            pl.col("rate_m").quantile(0.75, "linear").alias("p75_altitude_rate_m_per_day"),
        )
        weather: dict[str, Any] = {}
        for quantity in WEATHER:
            held = self._weather.get(quantity)
            weather[quantity] = held.value if held else None
            weather[f"{quantity}_observed_at"] = held.observed_at if held else None

        return (
            summary.with_columns(
                pl.lit(computed_at).alias("computed_at"),
                pl.lit(computed_at.date()).alias("computed_date"),
                pl.lit(int(self.window.total_seconds() // 3600)).alias("window_hours"),
                pl.lit(start).alias("window_start"),
                pl.lit(self.newest_epoch).alias("newest_epoch"),
                *(pl.lit(value).alias(name) for name, value in weather.items()),
            )
            .select([pl.col(name).cast(dtype) for name, dtype in NOWCAST_SCHEMA.items()])
            .sort("generation")
        )


def _sample(current: ElementSet, base: ElementSet | None) -> Sample:
    if base is None:
        return Sample(current.norad_id, current.epoch, current.bstar, None, False)
    days = (current.epoch - base.epoch).total_seconds() / 86_400.0
    rate = orbital.mean_motion_rate(base.mean_motion, current.mean_motion, days)
    return Sample(
        norad_id=current.norad_id,
        epoch=current.epoch,
        bstar=current.bstar,
        altitude_rate_km_per_day=orbital.altitude_rate_km_per_day(current.mean_motion, rate),
        is_manoeuvring=rate < MANOEUVRE_MEAN_MOTION_RATE,
    )
