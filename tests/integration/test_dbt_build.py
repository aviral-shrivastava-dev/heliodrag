"""Run the real dbt project against seeded fixtures.

The unit tests check the SQL's ingredients; this checks the whole transformation
end to end -- every model, every dbt test -- against data small enough to reason
about and build in a second. It uses the production models unmodified, so a
model that only works against the developer's warehouse fails here.

Nothing touches the real warehouse: the database is created per test in a
temporary directory, and dbt's artefacts go to a temporary target path so a
concurrent run cannot collide with this one.

The fixture is generated rather than captured. It uses real NORAD IDs taken
from the committed generation seed, so the generation join is genuinely
exercised, but no Space-Track data is involved -- the orbits are synthetic and
deliberately so.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import polars as pl
import pytest

from tests.integration.fixture_warehouse import START, run_dbt, summary, table

pytestmark = pytest.mark.integration


# -- the build -------------------------------------------------------------


def test_dbt_build_is_green_against_seeded_fixtures(seeded_warehouse: Path, tmp_path: Path) -> None:
    """Every model and every dbt test, against data built in this test."""
    result = run_dbt(seeded_warehouse, tmp_path)

    assert result.returncode == 0, result.stdout[-4000:]

    counts = summary(result.stdout)
    assert counts["ERROR"] == 0, result.stdout[-4000:]
    assert counts["PASS"] > 50, f"expected the whole suite to run, got {counts}"


def test_the_build_does_not_depend_on_the_session_time_zone(
    seeded_warehouse: Path, tmp_path: Path
) -> None:
    """Bronze timestamps are UTC instants. A model that casts them without
    naming a zone renders them in the session's -- on a laptop in India every
    epoch came out 5h30 late. In New York the fixture's 03:00 UTC epochs fall
    on the previous day, which the epoch_date tests catch."""
    result = run_dbt(seeded_warehouse, tmp_path, env={"DUCKDB_TIMEZONE": "America/New_York"})

    assert result.returncode == 0, result.stdout[-4000:]
    epochs = table(seeded_warehouse, "int_gp__deduplicated")["epoch_at"]
    assert set(epochs.dt.hour().to_list()) == {3}, "epoch_at must stay in UTC"


def test_the_marts_are_populated_and_joined(seeded_warehouse: Path, tmp_path: Path) -> None:
    """A green build over empty tables would prove nothing."""
    assert run_dbt(seeded_warehouse, tmp_path).returncode == 0

    satellites = table(seeded_warehouse, "dim_satellite")
    generations = table(seeded_warehouse, "dim_generation")
    decay = table(seeded_warehouse, "fct_daily_decay")

    assert satellites.height > 0
    assert generations.height > 1, "the fixture spans several generations"
    assert decay.height > 0

    # The generation join actually resolved: these are real seed IDs.
    assert "unknown" not in set(satellites["generation"].to_list())


def test_decay_is_derived_and_points_downward(seeded_warehouse: Path, tmp_path: Path) -> None:
    """The fixture's mean motion rises every day, so every satellite must be
    reported as losing altitude."""
    assert run_dbt(seeded_warehouse, tmp_path).returncode == 0

    decay = table(seeded_warehouse, "fct_daily_decay").filter(
        pl.col("altitude_rate_km_per_day").is_not_null()
    )

    assert decay.height > 0
    assert (decay["altitude_rate_km_per_day"] < 0).all(), "rising mean motion must fall"
    lowest = decay["mean_altitude_km"].min()
    assert isinstance(lowest, float) and lowest > 400


def test_the_storm_is_detected_and_superposed(seeded_warehouse: Path, tmp_path: Path) -> None:
    """The fixture contains exactly one storm, on day four."""
    assert run_dbt(seeded_warehouse, tmp_path).returncode == 0

    storms = table(seeded_warehouse, "fct_storm_epoch")

    assert storms.height > 0, "the -220 nT day should have been found"
    assert storms["storm_id"].n_unique() == 1
    assert storms["peak_date"].unique().to_list() == [START + dt.timedelta(days=4)]
    assert storms["peak_dst_nt"].min() == -220


def test_space_weather_is_joined_onto_every_day(seeded_warehouse: Path, tmp_path: Path) -> None:
    assert run_dbt(seeded_warehouse, tmp_path).returncode == 0

    decay = table(seeded_warehouse, "fct_daily_decay")

    assert decay["dst_min_nt"].null_count() == 0
    assert decay["f10_7_sfu"].null_count() == 0


# -- what the fixture proves about duplicates ------------------------------


def test_a_duplicated_bronze_row_does_not_reach_the_marts(
    seeded_warehouse: Path, tmp_path: Path
) -> None:
    """Bronze appends, so the marts must survive a re-ingested partition.

    This is the property ADR-0005 trades partition-replacement for, so it is
    asserted against the real models rather than assumed.
    """
    with duckdb.connect(str(seeded_warehouse)) as con:
        for name in ("gp_history", "omni", "satcat"):
            con.execute(f"insert into bronze.{name} select * from bronze.{name}")

    result = run_dbt(seeded_warehouse, tmp_path)

    assert result.returncode == 0, result.stdout[-4000:]

    decay = table(seeded_warehouse, "fct_daily_decay")
    keys = decay.select(["norad_id", "epoch_date"])
    assert keys.height == keys.unique().height, "duplicates reached the mart"


def test_a_correction_replaces_the_whole_element_set(
    seeded_warehouse: Path, tmp_path: Path
) -> None:
    """Space-Track corrects an element set by publishing a new gp_id for the
    same epoch. The whole correction must win -- including a value it leaves
    NULL. Deduplication is an aggregation (arg_max_null per column, because a
    window over the full history did not fit in memory); plain arg_max would
    skip the NULL and stitch the old bstar onto the new element set."""
    with duckdb.connect(str(seeded_warehouse)) as con:
        norad, epoch, epoch_utc, gp_id = con.execute(
            "select norad_id, epoch, epoch at time zone 'UTC', gp_id "
            "from bronze.gp_history order by norad_id, epoch limit 1"
        ).fetchone() or (None, None, None, None)
        con.execute(
            """
            insert into bronze.gp_history
            select * replace (gp_id + 1000000 as gp_id, 15.5 as mean_motion,
                              cast(null as double) as bstar)
            from bronze.gp_history where norad_id = ? and epoch = ?
            """,
            [norad, epoch],
        )

    result = run_dbt(seeded_warehouse, tmp_path)
    assert result.returncode == 0, result.stdout[-4000:]

    with duckdb.connect(str(seeded_warehouse), read_only=True) as con:
        rows = con.execute(
            "select gp_id, mean_motion_rev_per_day, bstar from int_gp__deduplicated "
            "where norad_id = ? and epoch_at = ?",
            [norad, epoch_utc],
        ).fetchall()

    assert rows == [(gp_id + 1000000, 15.5, None)]
