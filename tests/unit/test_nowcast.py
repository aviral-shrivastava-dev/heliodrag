"""The nowcast state, fed synthetic element sets.

Synthetic, and deliberately so: Space-Track data may not be committed (see
tests/fixtures/README.md). The orbits are simple enough to check by hand --
a mean motion that rises is a satellite falling -- and no number here is a
scientific result.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from starlink_drag.clients.swpc import Observation
from starlink_drag.science import orbital
from starlink_drag.stream.nowcast import (
    MANOEUVRE_MEAN_MOTION_RATE,
    NOWCAST_SCHEMA,
    ElementSet,
    NowcastState,
)

T0 = dt.datetime(2026, 9, 26, 0, tzinfo=dt.UTC)
GENERATIONS = {1: "v1.5", 2: "v1.5", 3: "v2-mini"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def es(
    norad: int, hours: float, mean_motion: float, bstar: float = 1e-4, gp: int = 0
) -> ElementSet:
    return ElementSet(
        norad,
        gp or int(hours * 10) + norad * 1000,
        T0 + dt.timedelta(hours=hours),
        mean_motion,
        bstar,
    )


def state(**kwargs: object) -> NowcastState:
    return NowcastState(GENERATIONS, **kwargs)  # type: ignore[arg-type]


def test_an_empty_state_gives_an_empty_snapshot_with_the_schema() -> None:
    snapshot = state().snapshot(T0)

    assert snapshot.is_empty()
    assert dict(snapshot.schema) == NOWCAST_SCHEMA


def test_one_element_set_gives_bstar_but_no_rate() -> None:
    nowcast = state()
    nowcast.add_element_set(es(1, 0, 15.06, bstar=2e-4))

    row = nowcast.snapshot(T0).row(0, named=True)

    assert row["generation"] == "v1.5"
    assert row["satellites"] == 1
    assert row["median_bstar"] == pytest.approx(2e-4)
    assert row["rated_satellites"] == 0
    assert row["median_altitude_rate_m_per_day"] is None


def test_a_rising_mean_motion_is_a_falling_satellite() -> None:
    nowcast = state()
    nowcast.add_element_set(es(1, 0, 15.06))
    nowcast.add_element_set(es(1, 12, 15.061))

    row = nowcast.snapshot(T0).row(0, named=True)
    rate = orbital.mean_motion_rate(15.06, 15.061, 0.5)
    expected = orbital.altitude_rate_km_per_day(15.061, rate) * 1000

    assert row["rated_satellites"] == 1
    assert row["median_altitude_rate_m_per_day"] == pytest.approx(expected)
    assert expected < 0


def test_a_repeat_or_older_element_set_is_ignored() -> None:
    nowcast = state()
    assert nowcast.add_element_set(es(1, 12, 15.06))

    assert not nowcast.add_element_set(es(1, 12, 15.06))
    assert not nowcast.add_element_set(es(1, 6, 15.05))


def test_a_rate_skips_element_sets_too_close_together() -> None:
    """Two element sets an hour apart differ by fitting noise; the rate is
    measured against the newest one at least six hours back."""
    nowcast = state()
    nowcast.add_element_set(es(1, 0, 15.060))
    nowcast.add_element_set(es(1, 12, 15.061))
    nowcast.add_element_set(es(1, 13, 15.0612))

    row = nowcast.snapshot(T0).row(0, named=True)
    rate = orbital.mean_motion_rate(15.060, 15.0612, 13 / 24)

    assert row["median_altitude_rate_m_per_day"] == pytest.approx(
        orbital.altitude_rate_km_per_day(15.0612, rate) * 1000
    )


def test_element_sets_too_far_apart_give_no_rate() -> None:
    nowcast = state()
    nowcast.add_element_set(es(1, 0, 15.06))
    nowcast.add_element_set(es(1, 24 * 4, 15.07))

    assert nowcast.snapshot(T0).row(0, named=True)["rated_satellites"] == 0


def test_a_satellite_under_thrust_is_counted_but_not_in_the_median() -> None:
    nowcast = state()
    nowcast.add_element_set(es(1, 0, 15.06))
    nowcast.add_element_set(es(1, 12, 15.061))  # falling
    nowcast.add_element_set(es(2, 0, 15.06))
    nowcast.add_element_set(es(2, 12, 15.05))  # rising fast: a thruster

    row = nowcast.snapshot(T0).row(0, named=True)
    falling = orbital.altitude_rate_km_per_day(15.061, orbital.mean_motion_rate(15.06, 15.061, 0.5))

    assert row["satellites"] == 2
    assert row["rated_satellites"] == 2
    assert row["manoeuvring_satellites"] == 1
    assert row["median_altitude_rate_m_per_day"] == pytest.approx(falling * 1000)


def test_each_generation_gets_its_own_row_and_unlabelled_satellites_are_unknown() -> None:
    nowcast = state()
    for norad in (1, 3, 99):
        nowcast.add_element_set(es(norad, 0, 15.06))

    assert nowcast.snapshot(T0)["generation"].to_list() == ["unknown", "v1.5", "v2-mini"]


def test_the_window_follows_the_newest_epoch_not_the_clock() -> None:
    nowcast = state(window=dt.timedelta(hours=24))
    nowcast.add_element_set(es(1, 0, 15.06))
    nowcast.add_element_set(es(3, 30, 15.06))

    snapshot = nowcast.snapshot(T0 + dt.timedelta(days=30))

    assert snapshot["generation"].to_list() == ["v2-mini"], "satellite 1 is 30 h behind the newest"
    assert snapshot["window_start"][0] == T0 + dt.timedelta(hours=6)


def test_the_newest_space_weather_wins_whatever_order_it_arrives_in() -> None:
    nowcast = state()
    nowcast.add_element_set(es(1, 0, 15.06))
    late = Observation("kp", T0 + dt.timedelta(hours=3), 4.0)
    early = Observation("kp", T0, 2.0)

    assert nowcast.add_observation(late)
    assert not nowcast.add_observation(early)
    row = nowcast.snapshot(T0).row(0, named=True)

    assert row["kp"] == 4.0
    assert row["kp_observed_at"] == late.observed_at
    assert row["dst"] is None


def test_a_replay_rebuilds_the_same_nowcast() -> None:
    """What lets the consumer restart from the topic with no checkpoint."""
    feed = [es(1, 0, 15.06), es(1, 12, 15.061), es(2, 1, 15.05), es(2, 14, 15.052)]
    first, second = state(), state()
    for item in feed:
        first.add_element_set(item)
    for item in feed + feed:  # a replay delivers everything again
        second.add_element_set(item)

    assert first.snapshot(T0).equals(second.snapshot(T0))


def test_the_snapshot_clears_the_dirty_flag() -> None:
    nowcast = state()
    nowcast.add_element_set(es(1, 0, 15.06))
    assert nowcast.dirty

    nowcast.snapshot(T0)

    assert not nowcast.dirty


def test_an_element_set_reads_from_a_message_with_or_without_an_offset() -> None:
    value = {
        "norad_id": 1,
        "gp_id": 7,
        "epoch": "2026-09-26T09:19:38.572032",
        "mean_motion": 15.0,
        "bstar": None,
    }

    naive = ElementSet.from_message(value)
    aware = ElementSet.from_message({**value, "epoch": "2026-09-26T09:19:38.572032+00:00"})

    assert naive == aware
    assert naive.epoch.tzinfo is not None
    assert naive.bstar is None


def test_the_manoeuvre_threshold_is_the_batch_paths() -> None:
    """The nowcast and fct_daily_decay must call the same satellites 'under thrust'."""
    sql = (
        REPOSITORY_ROOT / "transform" / "models" / "intermediate" / "int_decay__daily_rates.sql"
    ).read_text(encoding="utf-8")

    assert f"mean_motion_rate_per_day < {MANOEUVRE_MEAN_MOTION_RATE}" in sql
