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

import csv
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from starlink_drag.schemas import gp, omni, satcat

pytestmark = pytest.mark.integration

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRANSFORM_DIR = REPOSITORY_ROOT / "transform"
SEED = TRANSFORM_DIR / "seeds" / "starlink_generation_map.csv"

START = dt.date(2024, 5, 6)
DAYS = 10
SATELLITES_PER_GENERATION = 3

#: Small enough to build instantly, large enough for every model to have rows.
FIXTURE_VARS = {
    "min_satellites": 1,
    "max_satellites": 100000,
    "min_daily_decay_rows": 1,
}


def _seed_satellites() -> list[dict[str, str]]:
    """A few real NORAD IDs per generation, from the committed seed."""
    by_generation: dict[str, list[dict[str, str]]] = {}
    with SEED.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["generation"] == "unknown":
                continue
            bucket = by_generation.setdefault(row["generation"], [])
            if len(bucket) < SATELLITES_PER_GENERATION:
                bucket.append(row)
    return [row for rows in by_generation.values() for row in rows]


def _gp_rows(satellites: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Synthetic elements: a steady decay, one element set per satellite per day."""
    rows = []
    for index, satellite in enumerate(satellites):
        norad = satellite["norad_id"]
        for day in range(DAYS):
            epoch = dt.datetime.combine(START, dt.time(3, 0)) + dt.timedelta(days=day)
            # Mean motion creeps up, which is what decay looks like.
            mean_motion = 15.06 + 0.0002 * day + 0.0001 * index
            rows.append(
                {
                    "GP_ID": str(500_000 + index * 1000 + day),
                    "NORAD_CAT_ID": norad,
                    "OBJECT_NAME": satellite["object_name"],
                    "OBJECT_ID": "2024-001A",
                    "OBJECT_TYPE": "PAYLOAD",
                    "EPOCH": epoch.isoformat(),
                    "MEAN_MOTION": f"{mean_motion:.8f}",
                    "ECCENTRICITY": "0.00020000",
                    "INCLINATION": "53.0000",
                    "RA_OF_ASC_NODE": "300.0000",
                    "ARG_OF_PERICENTER": "80.0000",
                    "MEAN_ANOMALY": "270.0000",
                    "BSTAR": "0.00022000",
                    "MEAN_MOTION_DOT": "0.00003000",
                    "MEAN_MOTION_DDOT": "0.0",
                    "SEMIMAJOR_AXIS": "6925.000",
                    "PERIOD": "95.500",
                    "APOAPSIS": "548.000",
                    "PERIAPSIS": "546.000",
                    "REV_AT_EPOCH": "100",
                    "ELEMENT_SET_NO": "999",
                    "EPHEMERIS_TYPE": "0",
                    "CLASSIFICATION_TYPE": "U",
                    "RCS_SIZE": "LARGE",
                    "COUNTRY_CODE": "US",
                    "SITE": "AFETR",
                    "LAUNCH_DATE": satellite["launch_date"] or "2024-01-01",
                    "DECAY_DATE": None,
                    "CREATION_DATE": epoch.isoformat(),
                    "FILE": "1",
                    "TLE_LINE0": f"0 {satellite['object_name']}",
                    "TLE_LINE1": "1 x",
                    "TLE_LINE2": "2 x",
                }
            )
    return rows


def _satcat_rows(satellites: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "NORAD_CAT_ID": satellite["norad_id"],
            "INTLDES": "2024-001A",
            "SATNAME": satellite["object_name"],
            "OBJECT_TYPE": "PAYLOAD",
            "COUNTRY": "US",
            "LAUNCH": satellite["launch_date"] or "2024-01-01",
            "DECAY": None,
            "LAUNCH_YEAR": "2024",
            "LAUNCH_NUM": "1",
            "LAUNCH_PIECE": "A",
            "SITE": "AFETR",
            "PERIOD": "95.5",
            "INCLINATION": "53.0",
            "APOGEE": "548",
            "PERIGEE": "546",
            "RCS_SIZE": "LARGE",
            "RCSVALUE": "0",
            "CURRENT": "Y",
        }
        for satellite in satellites
    ]


def _omni_rows() -> list[dict[str, Any]]:
    """Hourly space weather containing one unambiguous storm."""
    rows = []
    for day in range(DAYS):
        date = START + dt.timedelta(days=day)
        # Day 4 is a severe storm; the rest are quiet.
        storm = day == 4
        for hour in range(24):
            rows.append(
                {
                    "Time": f"{date.isoformat()}T{hour:02d}:30:00.000Z",
                    "F10_INDEX1800": 180.0,
                    "KP1800": 90.0 if storm else 20.0,
                    "DST1800": -220.0 if storm else -8.0,
                    "AP_INDEX1800": 200.0 if storm else 7.0,
                }
            )
    return rows


@pytest.fixture
def seeded_warehouse(tmp_path: Path) -> Path:
    """A DuckDB database holding bronze tables shaped exactly like the real ones.

    Built through the production schema modules, so a change to the bronze
    column set breaks this test rather than silently diverging from it.
    """
    satellites = _seed_satellites()
    assert satellites, "the generation seed is empty"

    database = tmp_path / "fixture.duckdb"
    frames = {
        "gp_history": gp.to_frame(_gp_rows(satellites)),
        "satcat": satcat.to_frame(_satcat_rows(satellites), dt.date(2024, 5, 20)),
        "omni": omni.to_frame(_omni_rows()),
    }

    with duckdb.connect(str(database)) as con:
        con.execute("create schema if not exists bronze")
        for name, frame in frames.items():
            arrow = frame.to_arrow()  # noqa: F841 - referenced by the query below
            con.execute(f"create table bronze.{name} as select * from arrow")
        # These two exist in the real lake and dbt declares them as sources.
        con.execute(
            "create table bronze.quarantine "
            "(source varchar, ingest_date date, failure_reason varchar, payload varchar)"
        )
        con.execute(
            "create table bronze.ingest_audit "
            "(table_name varchar, source varchar, partition_from varchar, "
            "partition_to varchar, partition_count bigint, rows_written bigint, "
            "rows_quarantined bigint, ingest_timestamp timestamp, ingest_date date)"
        )
    return database


def _run_dbt(database: Path, tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ, DUCKDB_PATH=str(database))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dbt.cli.main",
            "build",
            "--project-dir",
            str(TRANSFORM_DIR),
            "--profiles-dir",
            str(TRANSFORM_DIR),
            # A private target path, so a concurrent dbt run cannot collide.
            "--target-path",
            str(tmp_path / "target"),
            "--vars",
            json.dumps(FIXTURE_VARS),
            *extra,
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _summary(output: str) -> dict[str, int]:
    """Parse dbt's `Done. PASS=.. WARN=.. ERROR=..` line.

    Parsed rather than substring-matched: a green run prints the literal text
    "ERROR=0", so `"ERROR" not in output` is always false and would fail every
    successful build.
    """
    for line in reversed(output.splitlines()):
        if "Done." in line and "PASS=" in line:
            counts = {}
            for token in line.split():
                if "=" in token:
                    key, _, value = token.partition("=")
                    if value.isdigit():
                        counts[key.strip()] = int(value)
            return counts
    raise AssertionError("no dbt summary line found in: " + output[-2000:])


def _table(database: Path, name: str) -> pl.DataFrame:
    with duckdb.connect(str(database), read_only=True) as con:
        return pl.from_arrow(con.execute(f"select * from main.{name}").arrow())  # type: ignore[return-value]


# -- the build -------------------------------------------------------------


def test_dbt_build_is_green_against_seeded_fixtures(seeded_warehouse: Path, tmp_path: Path) -> None:
    """Every model and every dbt test, against data built in this test."""
    result = _run_dbt(seeded_warehouse, tmp_path)

    assert result.returncode == 0, result.stdout[-4000:]

    summary = _summary(result.stdout)
    assert summary["ERROR"] == 0, result.stdout[-4000:]
    assert summary["PASS"] > 50, f"expected the whole suite to run, got {summary}"


def test_the_marts_are_populated_and_joined(seeded_warehouse: Path, tmp_path: Path) -> None:
    """A green build over empty tables would prove nothing."""
    assert _run_dbt(seeded_warehouse, tmp_path).returncode == 0

    satellites = _table(seeded_warehouse, "dim_satellite")
    generations = _table(seeded_warehouse, "dim_generation")
    decay = _table(seeded_warehouse, "fct_daily_decay")

    assert satellites.height > 0
    assert generations.height > 1, "the fixture spans several generations"
    assert decay.height > 0

    # The generation join actually resolved: these are real seed IDs.
    assert "unknown" not in set(satellites["generation"].to_list())


def test_decay_is_derived_and_points_downward(seeded_warehouse: Path, tmp_path: Path) -> None:
    """The fixture's mean motion rises every day, so every satellite must be
    reported as losing altitude."""
    assert _run_dbt(seeded_warehouse, tmp_path).returncode == 0

    decay = _table(seeded_warehouse, "fct_daily_decay").filter(
        pl.col("altitude_rate_km_per_day").is_not_null()
    )

    assert decay.height > 0
    assert (decay["altitude_rate_km_per_day"] < 0).all(), "rising mean motion must fall"
    lowest = decay["mean_altitude_km"].min()
    assert isinstance(lowest, float) and lowest > 400


def test_the_storm_is_detected_and_superposed(seeded_warehouse: Path, tmp_path: Path) -> None:
    """The fixture contains exactly one storm, on day four."""
    assert _run_dbt(seeded_warehouse, tmp_path).returncode == 0

    storms = _table(seeded_warehouse, "fct_storm_epoch")

    assert storms.height > 0, "the -220 nT day should have been found"
    assert storms["storm_id"].n_unique() == 1
    assert storms["peak_date"].unique().to_list() == [START + dt.timedelta(days=4)]
    assert storms["peak_dst_nt"].min() == -220


def test_space_weather_is_joined_onto_every_day(seeded_warehouse: Path, tmp_path: Path) -> None:
    assert _run_dbt(seeded_warehouse, tmp_path).returncode == 0

    decay = _table(seeded_warehouse, "fct_daily_decay")

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
        for table in ("gp_history", "omni", "satcat"):
            con.execute(f"insert into bronze.{table} select * from bronze.{table}")

    result = _run_dbt(seeded_warehouse, tmp_path)

    assert result.returncode == 0, result.stdout[-4000:]

    decay = _table(seeded_warehouse, "fct_daily_decay")
    keys = decay.select(["norad_id", "epoch_date"])
    assert keys.height == keys.unique().height, "duplicates reached the mart"
