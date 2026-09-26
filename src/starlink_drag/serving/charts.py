"""The explorer's charts, as pure functions from frames to Altair specs.

Three decisions a reader should know about:

**One y-axis per chart, always.** Decay rate and space weather have different
units, so they are never drawn against two y-scales on one plot -- the
alignment of two scales is arbitrary and invents correlations. Space weather
gets its own strip on the same time axis, and storm days are shaded across
every panel, so the two can be compared by position rather than by a second
scale.

**Colour follows the generation, never its position.** Each generation owns one
slot of a colour-blind-validated palette, so v1.5 is the same colour on every
chart and does not change when another generation is filtered out.

**Every panel is labelled.** Three light-mode colours sit below 3:1 contrast on
the page, so identity is carried by titles and the data tables as well as by
colour.
"""

from __future__ import annotations

import datetime as dt
import warnings
from typing import Final

import altair as alt
import polars as pl

# Validated with the dataviz palette checker in both modes: adjacent CVD
# separation >= 8.4, normal-vision >= 19.3. The research question's four
# generations take the first four slots.
_LIGHT: Final[dict[str, str]] = {
    "v1.0": "#2a78d6",
    "v1.5": "#eb6834",
    "v2-mini": "#1baf7a",
    "v2-mini-dtc": "#eda100",
    "v2-mini-opt": "#e87ba4",
    "v0.9": "#008300",
}
_DARK: Final[dict[str, str]] = {
    "v1.0": "#3987e5",
    "v1.5": "#d95926",
    "v2-mini": "#199e70",
    "v2-mini-dtc": "#c98500",
    "v2-mini-opt": "#d55181",
    "v0.9": "#008300",
}
_NEUTRAL: Final = "#898781"

WEATHER_INDICES: Final[dict[str, tuple[str, str]]] = {
    "Dst, worst of the day": ("dst_min_nt", "Dst (nT)"),
    "Kp, worst of the day": ("kp_max", "Kp"),
    "Ap, daily mean": ("ap_mean", "Ap (nT)"),
    "F10.7 solar flux": ("f10_7_sfu", "F10.7 (sfu)"),
}
"""Label shown to the reader -> (column, axis title)."""

STORM_DST_NT: Final = -50
"""Matches fct_space_weather_daily.is_storm_day and fct_storm_epoch."""


def colour(generation: str, *, dark: bool) -> str:
    return (_DARK if dark else _LIGHT).get(generation, _NEUTRAL)


def _ink(*, dark: bool) -> tuple[str, str]:
    """(line ink for the weather series, fill for storm shading)."""
    return ("#c3c2b7", "#383835") if dark else ("#52514e", "#e1e0d9")


def _time_axis(start: dt.date, end: dt.date, *, title: str | None = None) -> alt.X:
    # An explicit domain, not a resolved one: it is what keeps every panel's
    # dates vertically aligned, whatever each panel's data happens to span.
    return alt.X(
        "epoch_date:T",
        title=title,
        scale=alt.Scale(domain=[start.isoformat(), end.isoformat()]),
    )


def _storm_bands(weather: pl.DataFrame, start: dt.date, end: dt.date, *, dark: bool) -> alt.Chart:
    storms = weather.filter(pl.col("is_storm_day")).with_columns(
        (pl.col("epoch_date") + pl.duration(days=1)).alias("next_date")
    )
    bands: alt.Chart = (
        alt.Chart(storms)
        .mark_rect(color=_ink(dark=dark)[1], opacity=0.6)
        .encode(x=_time_axis(start, end), x2="next_date:T")
    )
    return bands


def weather_strip(
    weather: pl.DataFrame, index: str, start: dt.date, end: dt.date, *, dark: bool
) -> alt.LayerChart:
    """The forcing, on the same time axis as the decay panels below it."""
    column, title = WEATHER_INDICES[index]
    line = (
        alt.Chart(weather)
        .mark_line(strokeWidth=1.5, color=_ink(dark=dark)[0])
        .encode(
            x=_time_axis(start, end),
            y=alt.Y(f"{column}:Q", title=title),
            tooltip=[
                alt.Tooltip("epoch_date:T", title="Date"),
                alt.Tooltip(f"{column}:Q", title=title, format=".1f"),
            ],
        )
    )
    layers: list[alt.Chart] = [_storm_bands(weather, start, end, dark=dark), line]
    if column == "dst_min_nt":
        threshold = pl.DataFrame({"y": [STORM_DST_NT]})
        layers.append(alt.Chart(threshold).mark_rule(color=_NEUTRAL, strokeWidth=1).encode(y="y:Q"))
    return alt.LayerChart(layer=layers, title=f"Space weather: {index}", height=110)


SMOOTHING_DAYS: Final = 7

_RATES: Final = ("median_m_per_day", "p25_m_per_day", "p75_m_per_day")


def smooth(decay: pl.DataFrame, days: int = SMOOTHING_DAYS) -> pl.DataFrame:
    """A centred rolling mean of each generation's daily series.

    Daily rates are differences between consecutive orbit fits, so one fit's
    error enters two neighbouring days with opposite signs and the raw series
    zigzags: in 2024, consecutive daily medians for v1.0 in its operating
    shells have a lag-one autocorrelation of -0.69. Averaging over a week shows
    the trend. The window is centred so a storm is not shifted later, and
    measured in calendar days so a missing day is a gap, not a neighbour.
    """
    if decay.is_empty():
        return decay
    filled = decay.sort("generation", "epoch_date").upsample(
        time_column="epoch_date", every="1d", group_by="generation"
    )
    smoothed = filled.with_columns(
        pl.col(column)
        .rolling_mean(window_size=days, center=True, min_samples=days // 2 + 1)
        .over("generation")
        for column in _RATES
    )
    return smoothed.filter(pl.col("satellites").is_not_null())


def _shared_domain(decay: pl.DataFrame) -> list[float]:
    """A y-range every generation panel shares, so panels compare by height.

    The 1st to 99th percentile of the daily medians, widened to include zero.
    Not the full range: a handful of days dominated by satellites in commanded
    descent would otherwise flatten every other day to a line. Marks beyond the
    range are clipped, and the table below the chart still holds every value.
    """
    medians = decay["median_m_per_day"].drop_nulls()
    low = min(float(medians.quantile(0.01) or 0.0), 0.0)
    high = max(float(medians.quantile(0.99) or 0.0), 0.0)
    padding = 0.1 * (high - low) or 1.0
    return [low - padding, high + padding]


def decay_panel(
    frame: pl.DataFrame,
    generation: str,
    weather: pl.DataFrame,
    start: dt.date,
    end: dt.date,
    domain: list[float] | None,
    hover: alt.Parameter,
    *,
    dark: bool,
) -> alt.LayerChart:
    """One generation: daily median altitude change, with its middle half shaded."""
    hue = colour(generation, dark=dark)
    scale = alt.Scale(domain=domain) if domain else alt.Scale(zero=False)
    y = alt.Y("median_m_per_day:Q", title="m/day", scale=scale)
    base = alt.Chart(frame).encode(x=_time_axis(start, end))

    band = base.mark_area(color=hue, opacity=0.2, clip=True).encode(
        y=alt.Y("p25_m_per_day:Q", scale=scale), y2="p75_m_per_day:Q"
    )
    line = base.mark_line(color=hue, strokeWidth=2, clip=True).encode(y=y)
    targets = base.mark_point(size=80, opacity=0).encode(y=y).add_params(hover)
    rule = (
        base.mark_rule(color=_NEUTRAL)
        .encode(
            tooltip=[
                alt.Tooltip("epoch_date:T", title="Date"),
                alt.Tooltip("median_m_per_day:Q", title="Median m/day", format=".1f"),
                alt.Tooltip("p25_m_per_day:Q", title="25th percentile", format=".1f"),
                alt.Tooltip("p75_m_per_day:Q", title="75th percentile", format=".1f"),
                alt.Tooltip("satellites:Q", title="Satellites", format=","),
                alt.Tooltip("median_altitude_km:Q", title="Median altitude km", format=".0f"),
            ]
        )
        .transform_filter(hover)
    )
    zero = alt.Chart(pl.DataFrame({"y": [0.0]})).mark_rule(color=_NEUTRAL).encode(y="y:Q")

    return alt.LayerChart(
        layer=[_storm_bands(weather, start, end, dark=dark), band, zero, line, targets, rule],
        title=generation,
        height=120,
    )


def decay_panels(
    decay: pl.DataFrame,
    weather: pl.DataFrame,
    index: str,
    start: dt.date,
    end: dt.date,
    order: tuple[str, ...],
    *,
    shared_scale: bool,
    dark: bool,
) -> alt.VConcatChart:
    """The weather strip, then one panel per generation, all on one time axis."""
    present = [g for g in order if g in set(decay["generation"].to_list())]
    domain = _shared_domain(decay) if shared_scale and decay.height else None
    # One selection shared by every panel: hovering a date in one panel draws
    # the crosshair at that date in all of them.
    hover = alt.selection_point(
        name="day",
        fields=["epoch_date"],
        nearest=True,
        on="pointerover",
        empty=False,
        clear="pointerout",
    )
    panels = [
        decay_panel(
            decay.filter(pl.col("generation") == generation),
            generation,
            weather,
            start,
            end,
            domain,
            hover,
            dark=dark,
        )
        for generation in present
    ]
    with warnings.catch_warnings():
        # Altair warns whenever one selection spans several views. Here that is
        # the point: it is what links the crosshair across the panels.
        warnings.filterwarnings("ignore", message="Automatically deduplicated selection")
        return alt.vconcat(weather_strip(weather, index, start, end, dark=dark), *panels)


def storm_response(
    frame: pl.DataFrame, value: str, order: tuple[str, ...], *, dark: bool
) -> alt.LayerChart:
    """Superposed epoch: mean decay by day from storm peak, one line per generation."""
    present = [g for g in order if g in set(frame["generation"].to_list())]
    colours = alt.Scale(domain=present, range=[colour(g, dark=dark) for g in present])
    title = (
        "Change from the pre-storm mean (m/day)" if value == "excess_m_per_day" else "Mean m/day"
    )
    lines = (
        alt.Chart(frame)
        .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=36))
        .encode(
            x=alt.X(
                "days_from_peak:Q",
                title="Days from storm peak",
                scale=alt.Scale(domain=[-5, 10], nice=False),
                axis=alt.Axis(format="d", tickMinStep=1),
            ),
            y=alt.Y(f"{value}:Q", title=title),
            color=alt.Color("generation:N", scale=colours, sort=present, title="Generation"),
            tooltip=[
                alt.Tooltip("generation:N", title="Generation"),
                alt.Tooltip("days_from_peak:Q", title="Days from peak"),
                alt.Tooltip(f"{value}:Q", title=title, format=".1f"),
                alt.Tooltip("storms:Q", title="Storms"),
                alt.Tooltip("observations:Q", title="Satellite-days", format=","),
            ],
        )
    )
    peak = alt.Chart(pl.DataFrame({"x": [0]})).mark_rule(color=_NEUTRAL).encode(x="x:Q")
    zero = alt.Chart(pl.DataFrame({"y": [0.0]})).mark_rule(color=_NEUTRAL).encode(y="y:Q")
    return alt.LayerChart(layer=[peak, zero, lines], height=320)
