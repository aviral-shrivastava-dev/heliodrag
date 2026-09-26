"""Starlink Differential Drag Atlas -- the decay explorer.

Start it with ``uv run starlink-drag app``. This file is layout only: every
query is in ``starlink_drag.serving.marts``, which reads gold marts and nothing
else, and every chart is in ``starlink_drag.serving.charts``.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import streamlit as st

from starlink_drag.config import get_settings
from starlink_drag.science.generations import GENERATIONS, UNKNOWN
from starlink_drag.serving import charts, marts

RESEARCH_QUESTION = (
    "Do different Starlink hardware generations -- v1.0, v1.5, v2-mini and v2-mini DTC, which "
    "differ substantially in mass and cross-sectional area -- show statistically "
    "distinguishable orbital-decay sensitivity to the same space-weather forcing over "
    "Solar Cycle 25?"
)
ORDER = tuple(g for g in GENERATIONS if g != UNKNOWN)
DEFAULT_GENERATIONS = ("v1.0", "v1.5", "v2-mini", "v2-mini-dtc", "v2-mini-opt")
STORM_SEVERITY = {
    "Every storm (Dst at or below -50 nT)": -50,
    "Strong (-100 nT or below)": -100,
    "Severe (-200 nT or below)": -200,
}

st.set_page_config(page_title="Starlink Drag Atlas", layout="wide")


# Results are cached; connections are not. See serving/marts.py for why.
@st.cache_data(ttl=600, show_spinner=False)
def load_coverage(database: str) -> marts.Coverage:
    return marts.coverage(Path(database))


@st.cache_data(ttl=600, show_spinner=False)
def load_generations(database: str) -> pl.DataFrame:
    return marts.generations(Path(database))


@st.cache_data(ttl=600, show_spinner="Aggregating daily decay...")
def load_decay(database: str, part: marts.Slice) -> pl.DataFrame:
    return marts.daily_decay(Path(database), part)


@st.cache_data(ttl=600, show_spinner=False)
def load_weather(database: str, part: marts.Slice) -> pl.DataFrame:
    return marts.space_weather(Path(database), part)


@st.cache_data(ttl=600, show_spinner=False)
def load_storms(database: str, part: marts.Slice, max_peak_dst_nt: int) -> pl.DataFrame:
    return marts.storm_response(Path(database), part, max_peak_dst_nt)


def linked_date(name: str, fallback: dt.date, coverage: marts.Coverage) -> dt.date:
    """A date from the URL, e.g. ``?start=2024-01-01``, clamped to the data.

    So a particular view can be linked, and so the README's screenshot is of a
    view anyone can open, not of a hand-dragged slider.
    """
    try:
        value = dt.date.fromisoformat(st.query_params.get(name, ""))
    except ValueError:
        return fallback
    return min(max(value, coverage.first_date), coverage.last_date)


def filters(coverage: marts.Coverage) -> marts.Slice:
    """One row of controls above everything they scope."""
    with st.container(border=True):
        left, middle, right = st.columns([2, 3, 2])
        if coverage.first_date < coverage.last_date:
            start, end = left.slider(
                "Dates",
                min_value=coverage.first_date,
                max_value=coverage.last_date,
                value=(
                    linked_date("start", coverage.first_date, coverage),
                    linked_date("end", coverage.last_date, coverage),
                ),
                format="YYYY-MM-DD",
            )
        else:
            start = end = coverage.first_date
            left.markdown(f"**Dates:** {start.isoformat()} only")
        available = [g for g in ORDER if g in coverage.generations]
        chosen = middle.multiselect(
            "Generations",
            available,
            default=[g for g in DEFAULT_GENERATIONS if g in available],
        )
        lowest, highest = right.slider(
            "Altitude shells",
            min_value=coverage.lowest_shell_km,
            max_value=max(coverage.highest_shell_km, coverage.lowest_shell_km + 25.0),
            value=(coverage.lowest_shell_km, coverage.highest_shell_km),
            step=25.0,
            format="%d km",
            help="Air density falls steeply with height, so compare generations within a shell.",
        )
        ready = st.toggle(
            "Analysis-ready rows only",
            value=True,
            help="Excludes satellites under thrust, gaps longer than two days, rates no "
            "atmosphere could produce, and unlabelled generations.",
        )
    return marts.Slice(start, end, tuple(chosen), lowest, highest, analysis_ready_only=ready)


def decay_tab(database: str, part: marts.Slice, *, dark: bool) -> None:
    left, middle, right = st.columns([2, 1, 1])
    index = left.selectbox("Space-weather strip", list(charts.WEATHER_INDICES))
    smoothed = middle.toggle(
        f"Smooth over {charts.SMOOTHING_DAYS} days",
        value=True,
        help="Daily rates difference two noisy orbit fits, so the raw series zigzags.",
    )
    shared = right.toggle("Same scale for every generation", value=True)

    decay = load_decay(database, part)
    weather = load_weather(database, part)
    if decay.is_empty():
        st.info("No satellite-days match these filters. Widen the dates, shells or generations.")
        return

    st.caption(
        "Line: the median altitude change across that generation's satellites each day"
        + (f", averaged over a centred {charts.SMOOTHING_DAYS}-day window" if smoothed else "")
        + ". Band: the middle half of satellites. Negative means losing height. Grey "
        "columns: storm days, Dst at or below -50 nT."
        + (
            " Shared scale: 1st to 99th percentile of daily medians; beyond it, clipped."
            if shared
            else ""
        )
    )
    shown = charts.smooth(decay) if smoothed else decay
    st.altair_chart(
        charts.decay_panels(
            shown, weather, index, part.start, part.end, ORDER, shared_scale=shared, dark=dark
        ),
        width="stretch",
    )
    with st.expander("Show the data"):
        st.dataframe(decay, hide_index=True)
        st.dataframe(weather, hide_index=True)


def storm_tab(database: str, part: marts.Slice, *, dark: bool) -> None:
    left, right = st.columns(2)
    severity = left.selectbox("Storms", list(STORM_SEVERITY))
    relative = right.toggle("Relative to the five days before each peak", value=True)
    value = "excess_m_per_day" if relative else "mean_m_per_day"

    frame = load_storms(database, part, STORM_SEVERITY[severity])
    if frame.is_empty():
        st.info("No storms of that severity fall inside the chosen dates.")
        return

    storms = int(frame["storms"].max() or 0)
    st.caption(
        f"{storms} storm(s), aligned on the day Dst was lowest and averaged, weighted by "
        "satellite-days. Aligning many storms averages the slow solar-cycle trend away and "
        "leaves the fast response. Subtracting each storm's own pre-peak mean removes each "
        "generation's background -- station-keeping, commanded descent -- from the comparison."
    )
    st.altair_chart(charts.storm_response(frame, value, ORDER, dark=dark), width="stretch")
    with st.expander("Show the data"):
        st.dataframe(frame, hide_index=True)


def generations_tab(database: str) -> None:
    st.dataframe(
        load_generations(database),
        hide_index=True,
        column_config={
            "median_area_to_mass_proxy": st.column_config.NumberColumn(
                "area-to-mass proxy", format="%.2f", help="span^2 / dry mass; relative only"
            ),
            "decayed_pct": st.column_config.NumberColumn("decayed %", format="%.1f"),
        },
    )
    st.caption(
        "The area-to-mass proxy compares generations under one geometric assumption. It is "
        "not a ballistic coefficient. Generation labels come from Jonathan McDowell's GCAT "
        "(CC-BY): stated outright for Gen2, inferred from launch mass for Gen1."
    )


def about_tab() -> None:
    st.markdown(
        """
**Read these before drawing conclusions.** This is an explorer, not the analysis.

- **Operational satellites station-keep.** A thruster cancels the drag being measured,
  so a flat line can mean "no drag" or "drag, compensated". Storms, where drag briefly
  outruns the thrusters, are where the signal survives -- hence the storm tab.
- **Retired satellites are commanded down.** Most v1.0 satellites have been deorbited on
  purpose, which dominates any naive per-generation average.
- **Generation and date are entangled.** Each generation flew at a different point in the
  solar cycle, so the date must be held as well as the shell.
- **v2-mini-opt is not in the research question** but is the largest generation.
- **Raw daily rates zigzag.** Each is the difference of two orbit fits, so one fit's error
  lands on two neighbouring days with opposite signs. The smoothing toggle averages it out;
  it does not remove anything from the data.

Data: Space-Track.org element sets (not redistributed), NASA OMNI space weather via
SPDF HAPI (public domain), and GCAT generation labels (CC-BY).
"""
    )


def main() -> None:
    database = str(get_settings().duckdb_path)
    theme = st.context.theme
    dark = theme is not None and theme.type == "dark"

    st.title("Starlink Differential Drag Atlas")
    st.markdown(f"*{RESEARCH_QUESTION}*")

    try:
        coverage = load_coverage(database)
    except marts.WarehouseUnavailable as problem:
        st.info(str(problem))
        st.stop()

    tiles = st.columns(4)
    tiles[0].metric("Satellites", f"{coverage.satellites:,}")
    tiles[1].metric("Satellite-days", f"{coverage.satellite_days:,}")
    tiles[2].metric("First day", coverage.first_date.isoformat())
    tiles[3].metric("Last day", coverage.last_date.isoformat())

    part = filters(coverage)
    if not part.generations:
        st.info("Choose at least one generation.")
        st.stop()

    decay, storms, table, about = st.tabs(
        ["Decay by generation", "Storm response", "Generations", "Before you conclude"]
    )
    try:
        with decay:
            decay_tab(database, part, dark=dark)
        with storms:
            storm_tab(database, part, dark=dark)
    except marts.WarehouseUnavailable as problem:
        st.warning(str(problem))
    with table:
        generations_tab(database)
    with about:
        about_tab()
    st.caption(f"Reading {database}. Results are cached for ten minutes.")


main()
