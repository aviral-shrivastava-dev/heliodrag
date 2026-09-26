"""The explorer against real marts: a dbt build of the synthetic fixture.

Two layers. The queries are checked against what the fixture implies -- every
satellite decays, and there is exactly one storm, on day four. Then the whole
Streamlit script is run headless with Streamlit's own test harness, so a
broken chart spec, a query that fails on real column types or an exception in
any tab fails here rather than in front of someone.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import polars as pl
import pytest
from streamlit.testing.v1 import AppTest

from starlink_drag.serving import marts
from tests.integration.fixture_warehouse import DAYS, REPOSITORY_ROOT, START

pytestmark = pytest.mark.integration

APP = REPOSITORY_ROOT / "app" / "streamlit_app.py"
STORM_DAY = START + dt.timedelta(days=4)


def _everything(database: Path) -> marts.Slice:
    coverage = marts.coverage(database)
    return marts.Slice(
        coverage.first_date,
        coverage.last_date,
        coverage.generations,
        coverage.lowest_shell_km,
        coverage.highest_shell_km,
        min_satellites=1,
    )


# -- queries ---------------------------------------------------------------


def test_coverage_describes_the_fixture(built_warehouse: Path) -> None:
    coverage = marts.coverage(built_warehouse)

    assert coverage.first_date == START
    assert coverage.last_date == START + dt.timedelta(days=DAYS - 1)
    assert "unknown" not in coverage.generations
    assert len(coverage.generations) > 1


def test_every_daily_median_shows_decay(built_warehouse: Path) -> None:
    """The fixture's mean motion rises every day, so every rate is a fall."""
    decay = marts.daily_decay(built_warehouse, _everything(built_warehouse))

    assert decay.height > 0
    assert (decay["median_m_per_day"] < 0).all()
    assert (decay["p25_m_per_day"] <= decay["p75_m_per_day"]).all()


def test_days_with_too_few_satellites_are_left_out(built_warehouse: Path) -> None:
    part = _everything(built_warehouse)
    strict = marts.Slice(
        part.start,
        part.end,
        part.generations,
        part.lowest_shell_km,
        part.highest_shell_km,
        min_satellites=1000,
    )

    assert marts.daily_decay(built_warehouse, strict).is_empty()


def test_the_space_weather_series_has_the_one_storm(built_warehouse: Path) -> None:
    weather = marts.space_weather(built_warehouse, _everything(built_warehouse))

    assert weather.height == DAYS
    assert weather.filter(pl.col("is_storm_day"))["epoch_date"].to_list() == [STORM_DAY]


def test_the_storm_response_is_aligned_on_the_peak(built_warehouse: Path) -> None:
    frame = marts.storm_response(built_warehouse, _everything(built_warehouse), -50)

    assert frame.height > 0
    assert set(frame["storms"].to_list()) == {1}
    # The fixture starts four days before the storm, but its first day has no
    # rate -- there is no earlier element set to difference against.
    assert frame["days_from_peak"].min() == -3
    assert frame.filter(pl.col("days_from_peak") >= 0)["excess_m_per_day"].null_count() == 0


def test_a_severity_no_storm_reached_returns_nothing(built_warehouse: Path) -> None:
    frame = marts.storm_response(built_warehouse, _everything(built_warehouse), -300)

    assert frame.is_empty(), "the fixture's storm bottomed out at -220 nT"


def test_generations_match_the_dimension(built_warehouse: Path) -> None:
    with duckdb.connect(str(built_warehouse), read_only=True) as connection:
        expected = connection.execute("select count(*) from dim_generation").fetchone()

    assert expected is not None
    assert marts.generations(built_warehouse).height == expected[0]


# -- the app ---------------------------------------------------------------


def _run(database: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("DUCKDB_PATH", str(database))
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    return app


def test_the_explorer_renders_every_tab(
    built_warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _run(built_warehouse, monkeypatch)

    assert not app.exception
    assert app.title[0].value == "Starlink Differential Drag Atlas"
    assert [tab.label for tab in app.tabs] == [
        "Decay by generation",
        "Storm response",
        "Generations",
        "Before you conclude",
    ]
    assert not app.warning, [w.value for w in app.warning]


def test_the_raw_series_renders_as_well_as_the_smoothed_one(
    built_warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _run(built_warehouse, monkeypatch)

    smoothing = next(t for t in app.toggle if t.label.startswith("Smooth"))
    smoothing.set_value(False).run()

    assert not app.exception


def test_a_view_can_be_linked_by_its_dates(
    built_warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DUCKDB_PATH", str(built_warehouse))
    app = AppTest.from_file(str(APP), default_timeout=60)
    later = START + dt.timedelta(days=3)
    app.query_params["start"] = later.isoformat()
    app.query_params["end"] = "2099-01-01"  # past the data: clamped, not an error
    app.run()

    assert not app.exception
    assert app.slider[0].value == (later, START + dt.timedelta(days=DAYS - 1))


def test_choosing_no_generation_asks_for_one(
    built_warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _run(built_warehouse, monkeypatch)

    app.multiselect[0].set_value([]).run()

    assert not app.exception
    assert "Choose at least one generation" in app.info[0].value


def test_without_a_warehouse_it_says_how_to_build_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _run(tmp_path / "atlas.duckdb", monkeypatch)

    assert not app.exception
    assert "uv run starlink-drag demo" in app.info[0].value
