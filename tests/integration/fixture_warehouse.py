"""A small warehouse built from synthetic bronze, for tests that need real marts.

The fixture is generated rather than captured. It uses real NORAD IDs taken
from the committed generation seed, so the generation join is genuinely
exercised, but no Space-Track data is involved -- the orbits are synthetic and
deliberately so. It contains one unambiguous storm, on day four.

Nothing touches the real warehouse: callers pass a database path in a
temporary directory, and dbt's artefacts go to a private target path so a
concurrent run cannot collide with another.
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

from starlink_drag.schemas import gp, omni, satcat

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


def build_bronze(database: Path) -> None:
    """A DuckDB database holding bronze tables shaped exactly like the real ones.

    Built through the production schema modules, so a change to the bronze
    column set breaks this test rather than silently diverging from it.
    """
    satellites = _seed_satellites()
    assert satellites, "the generation seed is empty"

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


def run_dbt(database: Path, work_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
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
            str(work_dir / "target"),
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


def summary(output: str) -> dict[str, int]:
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


def table(database: Path, name: str) -> pl.DataFrame:
    with duckdb.connect(str(database), read_only=True) as con:
        return pl.from_arrow(con.execute(f"select * from main.{name}").arrow())  # type: ignore[return-value]
