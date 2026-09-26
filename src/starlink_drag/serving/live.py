"""The live nowcast page's data and charts. Pure functions of the nowcast table.

Kept apart from ``marts`` and ``charts``, which serve the batch warehouse: this
reads the streaming path's own Iceberg table (``stream.table``) and nothing
from the warehouse, so the explorer's batch pages cannot be affected by it.
Charts follow ``charts``' rules -- one y-axis each, colour by generation.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

import altair as alt
import polars as pl

from starlink_drag.serving.charts import colour

HISTORY: Final = dt.timedelta(hours=48)

#: A UTC scale, because Vega-Lite otherwise draws times in the *browser's*
#: zone under a title that says UTC: in India, 18:20 UTC was labelled 23:50.
#: An explicit tick format, because snapshots are minutes apart and the default
#: drops to milliseconds when there are only a few of them.
_TIME: Final = alt.X(
    "computed_at:T",
    title="Computed (UTC)",
    scale=alt.Scale(type="utc"),
    axis=alt.Axis(format="%d %b %H:%M"),
)


@dataclass(frozen=True, slots=True)
class Freshness:
    computed_at: dt.datetime
    newest_epoch: dt.datetime
    snapshot_age: dt.timedelta
    epoch_age: dt.timedelta
    stale: bool
    """No snapshot for longer than the consumer should ever go without one."""


def freshness(latest: pl.DataFrame, now: dt.datetime, *, stale_after: dt.timedelta) -> Freshness:
    computed_at: dt.datetime = latest["computed_at"].max()  # type: ignore[assignment]
    newest_epoch: dt.datetime = latest["newest_epoch"].max()  # type: ignore[assignment]
    return Freshness(
        computed_at=computed_at,
        newest_epoch=newest_epoch,
        snapshot_age=now - computed_at,
        epoch_age=now - newest_epoch,
        stale=now - computed_at > stale_after,
    )


def generation_table(latest: pl.DataFrame) -> pl.DataFrame:
    """The latest snapshot, one row per generation, in reader's units."""
    return latest.select(
        pl.col("generation").alias("Generation"),
        pl.col("satellites").alias("Satellites"),
        pl.col("rated_satellites").alias("With a rate"),
        pl.col("manoeuvring_satellites").alias("Under thrust"),
        pl.col("median_bstar").alias("Median BSTAR (1/earth radii)"),
        pl.col("median_altitude_rate_m_per_day").round(1).alias("Median altitude change (m/day)"),
        pl.col("p25_altitude_rate_m_per_day").round(1).alias("25th pct"),
        pl.col("p75_altitude_rate_m_per_day").round(1).alias("75th pct"),
    )


def recent(frame: pl.DataFrame, now: dt.datetime) -> pl.DataFrame:
    return frame.filter(pl.col("computed_at") >= now - HISTORY)


def history_chart(
    frame: pl.DataFrame, column: str, title: str, *, dark: bool, number_format: str = ",.1f"
) -> alt.Chart:
    """One line per generation over the snapshots' computation times.

    BSTAR is of order 1e-4, below what the default number format can show, so
    the format is the caller's.
    """
    data = frame.select("computed_at", "generation", column).drop_nulls(column)
    generations = sorted(data["generation"].unique().to_list())
    chart: alt.Chart = (
        alt.Chart(data)
        .mark_line(point=True)
        .encode(
            x=_TIME,
            y=alt.Y(f"{column}:Q", title=title, axis=alt.Axis(format=number_format)),
            color=alt.Color(
                "generation:N",
                scale=alt.Scale(
                    domain=generations, range=[colour(g, dark=dark) for g in generations]
                ),
                legend=alt.Legend(title="Generation", orient="right"),
            ),
            tooltip=["generation:N", "computed_at:T", alt.Tooltip(f"{column}:Q", format=".3~e")],
        )
        .properties(height=220)
    )
    return chart


def weather_chart(frame: pl.DataFrame) -> alt.Chart:
    """The 1-minute estimated Kp each snapshot saw."""
    data = frame.select("computed_at", "kp_estimated").unique("computed_at").drop_nulls()
    chart: alt.Chart = (
        alt.Chart(data)
        .mark_line(point=True, interpolate="step-after")
        .encode(
            x=_TIME,
            y=alt.Y(
                "kp_estimated:Q",
                title="Kp (estimated)",
                scale=alt.Scale(domain=[0, 9]),
                axis=alt.Axis(values=[0, 3, 5, 7, 9]),
            ),
            tooltip=["computed_at:T", "kp_estimated:Q"],
        )
        .properties(height=160)
    )
    return chart
