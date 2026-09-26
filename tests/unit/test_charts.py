"""The explorer's charts: fixed colours, one y-axis each, honest smoothing.

Frames here are small and synthetic -- shapes, not measurements -- because what
is under test is how the charts treat data, not the data.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl
import pytest

from starlink_drag.serving import charts

START = dt.date(2024, 5, 1)
END = dt.date(2024, 5, 31)


def _decay(generation: str, medians: list[float], *, skip: tuple[int, ...] = ()) -> pl.DataFrame:
    days = [d for d in range(len(medians)) if d not in skip]
    return pl.DataFrame(
        {
            "epoch_date": [START + dt.timedelta(days=d) for d in days],
            "generation": [generation] * len(days),
            "satellites": [100] * len(days),
            "median_m_per_day": [medians[d] for d in days],
            "p25_m_per_day": [medians[d] - 5 for d in days],
            "p75_m_per_day": [medians[d] + 5 for d in days],
            "median_altitude_km": [550.0] * len(days),
        }
    )


def _weather(storm_days: tuple[int, ...] = (3,)) -> pl.DataFrame:
    days = range(31)
    return pl.DataFrame(
        {
            "epoch_date": [START + dt.timedelta(days=d) for d in days],
            "f10_7_sfu": [150.0] * 31,
            "kp_max": [2.0] * 31,
            "ap_mean": [7.0] * 31,
            "dst_min_nt": [-120 if d in storm_days else -10 for d in days],
            "is_storm_day": [d in storm_days for d in days],
        }
    )


def _layers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = spec["layer"]
    return layers


# -- colour ----------------------------------------------------------------


def test_each_generation_keeps_one_colour_whatever_else_is_shown() -> None:
    """Colour follows the entity: filtering one generation out must not
    repaint the others."""
    alone = charts.colour("v1.5", dark=False)
    assert alone == charts.colour("v1.5", dark=False)
    colours = {charts.colour(g, dark=False) for g in ("v1.0", "v1.5", "v2-mini", "v2-mini-dtc")}
    assert len(colours) == 4, "the research question's generations must be distinguishable"


def test_dark_mode_has_its_own_steps() -> None:
    assert charts.colour("v1.0", dark=True) != charts.colour("v1.0", dark=False)


def test_an_unlabelled_generation_is_neutral_not_a_series_colour() -> None:
    series = {charts.colour(g, dark=False) for g in ("v1.0", "v1.5", "v2-mini")}
    assert charts.colour("unknown", dark=False) not in series


# -- smoothing -------------------------------------------------------------


def test_smoothing_is_centred_so_a_storm_is_not_shifted_later() -> None:
    medians = [0.0] * 15
    medians[7] = -70.0
    smoothed = charts.smooth(_decay("v1.5", medians))

    # A week-long window spreads one day's spike over the three days either
    # side of it. A trailing window would spread it over the six days after.
    touched = smoothed.filter(pl.col("median_m_per_day") < 0)["epoch_date"].to_list()
    assert touched == [START + dt.timedelta(days=d) for d in range(4, 11)]


def test_a_missing_day_is_a_gap_not_a_neighbour() -> None:
    medians = [0.0, 0.0, 0.0, 0.0, 99.0, 0.0, 0.0, 0.0, 0.0]
    smoothed = charts.smooth(_decay("v1.5", medians, skip=(4,)), days=3)

    assert smoothed.height == 8, "only days that were observed are returned"
    assert smoothed["median_m_per_day"].max() == 0.0, "the missing day contributed nothing"


def test_smoothing_keeps_generations_apart() -> None:
    frame = pl.concat([_decay("v1.0", [-50.0] * 9), _decay("v1.5", [0.0] * 9)])
    smoothed = charts.smooth(frame)

    assert smoothed.filter(pl.col("generation") == "v1.5")["median_m_per_day"].max() == 0.0


def test_smoothing_nothing_returns_nothing() -> None:
    empty = _decay("v1.5", [])
    assert charts.smooth(empty).is_empty()


# -- the shared scale ------------------------------------------------------


def test_the_shared_scale_always_shows_zero() -> None:
    low, high = charts._shared_domain(_decay("v1.5", [-30.0, -20.0, -25.0]))
    assert low < -30.0 < 0.0 < high


def test_one_extreme_day_does_not_flatten_every_panel() -> None:
    """A commanded descent can post -800 m/day; the scale follows the bulk."""
    medians = [-10.0 + (d % 3) for d in range(200)]
    medians[100] = -800.0
    low, _ = charts._shared_domain(_decay("v1.0", medians))
    assert low > -100.0


# -- the charts ------------------------------------------------------------


def test_panels_follow_the_fixed_generation_order() -> None:
    frame = pl.concat([_decay("v2-mini", [-5.0] * 9), _decay("v1.0", [-10.0] * 9)])
    order = ("v0.9", "v1.0", "v1.5", "v2-mini")

    spec = charts.decay_panels(
        frame, _weather(), "Dst, worst of the day", START, END, order, shared_scale=True, dark=False
    ).to_dict()

    titles = [panel["title"] for panel in spec["vconcat"]]
    assert titles[0].startswith("Space weather")
    assert titles[1:] == ["v1.0", "v2-mini"]


def test_every_panel_has_one_y_axis_and_the_same_dates() -> None:
    """Space weather and decay never share a plot: no dual axes, anywhere."""
    spec = charts.decay_panels(
        _decay("v1.5", [-5.0] * 9),
        _weather(),
        "F10.7 solar flux",
        START,
        END,
        ("v1.5",),
        shared_scale=False,
        dark=False,
    ).to_dict()

    for panel in spec["vconcat"]:
        y_fields = {
            layer["encoding"]["y"]["field"]
            for layer in _layers(panel)
            if "y" in layer.get("encoding", {}) and "field" in layer["encoding"]["y"]
        }
        assert len(y_fields - {"y", "p25_m_per_day"}) == 1, f"{panel['title']}: {y_fields}"
        for layer in _layers(panel):
            x = layer.get("encoding", {}).get("x", {})
            if x.get("field") == "epoch_date":
                assert x["scale"]["domain"] == [START.isoformat(), END.isoformat()]


def test_the_storm_threshold_is_drawn_only_on_the_dst_strip() -> None:
    dst = charts.weather_strip(_weather(), "Dst, worst of the day", START, END, dark=False)
    flux = charts.weather_strip(_weather(), "F10.7 solar flux", START, END, dark=False)

    assert len(_layers(dst.to_dict())) == len(_layers(flux.to_dict())) + 1


@pytest.mark.parametrize("value", ["excess_m_per_day", "mean_m_per_day"])
def test_the_storm_response_colours_match_the_panels(value: str) -> None:
    frame = pl.DataFrame(
        {
            "generation": ["v1.0", "v1.0", "v2-mini", "v2-mini"],
            "days_from_peak": [0, 1, 0, 1],
            "storms": [3] * 4,
            "observations": [500] * 4,
            "mean_m_per_day": [-30.0, -20.0, -25.0, -15.0],
            "excess_m_per_day": [-10.0, -5.0, -8.0, -3.0],
        }
    )
    spec = charts.storm_response(frame, value, ("v1.0", "v1.5", "v2-mini"), dark=True).to_dict()

    lines = next(layer for layer in _layers(spec) if layer.get("encoding", {}).get("color"))
    scale = lines["encoding"]["color"]["scale"]
    assert scale["domain"] == ["v1.0", "v2-mini"]
    assert scale["range"] == [charts.colour("v1.0", dark=True), charts.colour("v2-mini", dark=True)]
