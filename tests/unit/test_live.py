"""The live page's helpers: freshness, the table, and the chart specs.
Synthetic nowcasts; see tests/fixtures/README.md."""

from __future__ import annotations

import datetime as dt

import polars as pl

from starlink_drag.clients.swpc import Observation
from starlink_drag.serving import live
from starlink_drag.stream.nowcast import ElementSet, NowcastState
from starlink_drag.stream.table import latest

NOW = dt.datetime(2026, 9, 26, 18, 20, tzinfo=dt.UTC)


def _nowcasts(*computed: dt.datetime) -> pl.DataFrame:
    frames = []
    for at in computed:
        state = NowcastState({1: "v1.5", 2: "v2-mini"})
        for norad in (1, 2):
            state.add_element_set(ElementSet(norad, 1, at - dt.timedelta(hours=16), 15.06, 1e-4))
            state.add_element_set(ElementSet(norad, 2, at - dt.timedelta(hours=4), 15.061, 2e-4))
        state.add_observation(Observation("kp_estimated", at, 3.0))
        frames.append(state.snapshot(at))
    return pl.concat(frames)


def test_freshness_measures_both_the_snapshot_and_the_data() -> None:
    frame = latest(_nowcasts(NOW - dt.timedelta(minutes=10)))

    fresh = live.freshness(frame, NOW, stale_after=dt.timedelta(minutes=35))

    assert fresh.snapshot_age == dt.timedelta(minutes=10)
    assert fresh.epoch_age == dt.timedelta(hours=4, minutes=10)
    assert not fresh.stale
    assert live.freshness(
        frame, NOW + dt.timedelta(hours=1), stale_after=dt.timedelta(minutes=35)
    ).stale


def test_latest_keeps_one_row_per_generation_the_newest() -> None:
    frame = _nowcasts(NOW - dt.timedelta(hours=1), NOW)

    newest = latest(frame)

    assert newest["generation"].to_list() == ["v1.5", "v2-mini"]
    assert (newest["computed_at"] == NOW).all()


def test_the_table_is_in_readers_units() -> None:
    table = live.generation_table(latest(_nowcasts(NOW)))

    assert table.columns[0] == "Generation"
    assert (table["Median altitude change (m/day)"] < 0).all()


def test_times_are_drawn_in_utc_not_the_browsers_zone() -> None:
    history = live.recent(_nowcasts(NOW - dt.timedelta(hours=1), NOW), NOW)

    for chart in (
        live.history_chart(history, "median_bstar", "BSTAR", dark=False, number_format=".1e"),
        live.weather_chart(history),
    ):
        spec = chart.to_dict()
        assert spec["encoding"]["x"]["scale"]["type"] == "utc"


def test_recent_drops_snapshots_older_than_the_history() -> None:
    frame = _nowcasts(NOW - dt.timedelta(hours=72), NOW)

    assert live.recent(frame, NOW)["computed_at"].unique().to_list() == [NOW]
