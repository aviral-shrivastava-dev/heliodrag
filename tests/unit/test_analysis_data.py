"""The analysis's reading of the gold marts, on a warehouse built by hand.

Synthetic, so that every rule has an answer known in advance: a satellite
raised to 550 km, held there, then lowered; one clean storm, one whose baseline
holds another storm's days, and one too late for its response window.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from analysis.models import data

START = dt.date(2024, 1, 1)
CLEAN = dt.date(2024, 2, 15)
CROWDED = dt.date(2024, 3, 1)
LATE = data.PERIOD_END - dt.timedelta(days=1)


def _day(offset: int) -> dt.date:
    return START + dt.timedelta(days=offset)


def _satellite_days() -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for offset in range(121):  # 2024-01-01 .. 2024-04-30
        day = _day(offset)
        if day < dt.date(2024, 1, 11):
            shell, altitude, rate = 400.0, 400.0 + offset * 10, 10.0  # raising
        elif day < dt.date(2024, 4, 1):
            storm = 0 <= (day - CLEAN).days <= 2
            shell, altitude, rate = 550.0, 555.0, -0.035 if storm else -0.005
        else:
            shell, altitude, rate = 525.0, 540.0 - (day - dt.date(2024, 4, 1)).days * 0.1, -0.1
        rows.append((1, day, "v1.5", shell, altitude, rate, 10.0, 150.0, -20, True))
    return rows


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "atlas.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("""create table fct_daily_decay (norad_id bigint, epoch_date date,
            generation varchar, altitude_shell_km double, mean_altitude_km double,
            altitude_rate_km_per_day double, ap_mean double, f10_7_sfu double,
            dst_min_nt integer, is_analysis_ready boolean)""")
        con.executemany(
            "insert into fct_daily_decay values (?,?,?,?,?,?,?,?,?,?)", _satellite_days()
        )
        con.execute("""create table fct_storm_epoch (storm_id bigint, peak_date date,
            peak_dst_nt integer, peak_ap integer, f10_7_sfu double)""")
        con.executemany(
            "insert into fct_storm_epoch values (?,?,?,?,?)",
            [(1, CLEAN, -150, 150, 160.0), (2, CROWDED, -80, 80, 150.0), (3, LATE, -90, 90, 140.0)],
        )
        con.execute("""create table fct_space_weather_daily (epoch_date date, f10_7_sfu double,
            is_storm_day boolean)""")
        storm_days = {CLEAN, CROWDED, CROWDED - dt.timedelta(days=4), LATE}
        con.executemany(
            "insert into fct_space_weather_daily values (?,?,?)",
            [(_day(i), 150.0, _day(i) in storm_days) for i in range(121)] + [(LATE, 140.0, True)],
        )
        con.execute("""create table dim_generation (generation varchar,
            median_area_to_mass_proxy double, satellites bigint)""")
        con.execute("insert into dim_generation values ('v1.5', 0.28, 1)")
    return path


def test_a_satellites_life_is_split_into_three_phases(warehouse: Path) -> None:
    phases = data.phase_days(data.connect(warehouse))

    days = {row["phase"]: row["satellite_days"] for row in phases.iter_rows(named=True)}
    assert days == {"raising": 10, "operational": 81, "descending": 30}


def test_storms_are_excluded_for_a_crowded_baseline_or_a_late_peak(warehouse: Path) -> None:
    storms = data.storms(data.connect(warehouse))

    reasons = dict(
        zip(storms["storm_id"].to_list(), storms["excluded_because"].to_list(), strict=True)
    )
    assert reasons == {
        1: None,
        2: "storm days in the baseline window",
        3: "response window after the period",
    }


def test_a_storm_response_is_the_satellites_own_rate_change(warehouse: Path) -> None:
    responses = data.storm_responses(data.connect(warehouse))

    (row,) = responses.iter_rows(named=True)
    assert row["storm_id"] == 1
    assert row["shell_km"] == 550.0
    assert (row["baseline_days"], row["response_days"]) == (5, 3)
    assert row["baseline_m"] == pytest.approx(-5.0)
    assert row["delta_m"] == pytest.approx(-30.0), "35 m/day in the storm against 5 before"


def test_the_curve_is_relative_to_the_baseline(warehouse: Path) -> None:
    curves = data.epoch_curves(data.connect(warehouse))

    by_day = dict(zip(curves["day"].to_list(), curves["relative_rate_m"].to_list(), strict=True))
    assert by_day[-3] == pytest.approx(0.0)
    assert by_day[1] == pytest.approx(-30.0)


def test_only_months_that_lose_altitude_count_as_descent(warehouse: Path) -> None:
    sums = data.descent_sums(data.connect(warehouse))

    assert sums["month"].dt.month().to_list() == [4], "April falls 3 km; March is held"
    assert sums["n"].to_list() == [30]


def test_the_period_is_pinned() -> None:
    """Figures must not change because the warehouse grew overnight."""
    assert dt.date(2020, 1, 1) == data.PERIOD_START
    assert dt.date(2026, 9, 16) == data.PERIOD_END
