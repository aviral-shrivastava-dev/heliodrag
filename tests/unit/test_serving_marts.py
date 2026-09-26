"""The explorer's query layer: gold marts only, and failures a person can act on.

The rule that the explorer reads gold and nothing else is enforced here rather
than trusted: every query is scanned for the tables it reads, and the list of
gold marts is checked against the tag dbt actually applies. The queries'
results against a real build are tested in tests/integration/test_serving.py.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import duckdb
import pytest

from starlink_drag.serving import marts

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPOSITORY_ROOT / "transform" / "target" / "manifest.json"

QUERIES = {name: value for name, value in vars(marts).items() if name.endswith("_SQL")}
READS = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_.]*)", re.IGNORECASE)
CTE = re.compile(r"\b([a-z_][a-z0-9_]*)\s+as\s*\(", re.IGNORECASE)


def _tables_read(sql: str) -> set[str]:
    return set(READS.findall(sql)) - set(CTE.findall(sql))


def _slice() -> marts.Slice:
    return marts.Slice(dt.date(2024, 1, 1), dt.date(2024, 12, 31), ("v1.5",), 500.0, 575.0)


def test_there_are_queries_to_check() -> None:
    assert len(QUERIES) >= 5


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_every_query_reads_only_gold_marts(name: str) -> None:
    tables = _tables_read(QUERIES[name])

    assert tables, f"{name} reads no table at all; the scan is broken"
    assert tables <= marts.GOLD_MARTS, f"{name} reads {sorted(tables - marts.GOLD_MARTS)}"


def test_the_gold_list_is_exactly_what_dbt_tags_gold() -> None:
    """A mart added to dbt without being added here, or the reverse, fails."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    gold = {
        node["name"]
        for node in manifest["nodes"].values()
        if node["resource_type"] == "model" and "gold" in node["tags"]
    }

    assert gold == marts.GOLD_MARTS


def test_every_parameter_a_decay_query_uses_is_supplied() -> None:
    used = set(re.findall(r"\$(\w+)", marts.DAILY_DECAY_SQL))

    assert used <= set(_slice().params())


def test_every_parameter_the_storm_query_uses_is_supplied() -> None:
    used = set(re.findall(r"\$(\w+)", marts.STORM_RESPONSE_SQL))
    supplied = set(_slice().params()) - {"analysis_ready_only", "min_satellites"}

    assert used <= supplied | {"max_peak_dst_nt"}


def test_a_missing_warehouse_says_how_to_build_one(tmp_path: Path) -> None:
    with pytest.raises(marts.WarehouseUnavailable, match="starlink-drag demo"):
        marts.coverage(tmp_path / "atlas.duckdb")


def test_a_warehouse_without_marts_says_so(tmp_path: Path) -> None:
    database = tmp_path / "atlas.duckdb"
    duckdb.connect(str(database)).close()

    with pytest.raises(marts.WarehouseUnavailable, match="no gold marts"):
        marts.coverage(database)


def test_marts_with_no_decay_rows_say_so(tmp_path: Path) -> None:
    database = tmp_path / "atlas.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "create table fct_daily_decay (norad_id bigint, epoch_date date, "
            "generation varchar, altitude_shell_km double, is_analysis_ready boolean)"
        )

    with pytest.raises(marts.WarehouseUnavailable, match="no decay data"):
        marts.coverage(database)


def test_the_connection_is_closed_after_every_query(tmp_path: Path) -> None:
    """The explorer must never hold the file: a held reader blocks dbt's writer."""
    database = tmp_path / "atlas.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("create table fct_space_weather_daily (epoch_date date)")

    marts.read(database, "select * from fct_space_weather_daily")

    # A read-write connection opens only if no other connection holds the file.
    duckdb.connect(str(database)).close()
