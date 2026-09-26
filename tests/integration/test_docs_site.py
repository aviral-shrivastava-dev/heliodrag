"""The published dbt docs: built for real, and built from nothing.

The page is what GitHub Pages serves, so it is built here exactly as the docs
workflow builds it. The second test is the reason it may be published at all:
the warehouse it is generated from holds no rows of Space-Track data.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from starlink_drag import docs_site
from tests.integration.fixture_warehouse import REPOSITORY_ROOT

pytestmark = pytest.mark.integration


def test_the_empty_bronze_has_every_source_and_no_rows(tmp_path: Path) -> None:
    database = tmp_path / "atlas.duckdb"

    docs_site.empty_bronze(database)

    with duckdb.connect(str(database), read_only=True) as connection:
        tables = connection.execute(
            "select table_name, estimated_size from duckdb_tables() where schema_name = 'bronze'"
        ).fetchall()
        norad_type = connection.execute(
            "select data_type from duckdb_columns() "
            "where schema_name = 'bronze' and table_name = 'satcat' and column_name = 'norad_id'"
        ).fetchone()

    assert {name for name, _ in tables} == {
        "gp_history",
        "satcat",
        "omni",
        "quarantine",
        "ingest_audit",
    }
    assert all(size == 0 for _, size in tables)
    assert norad_type == ("BIGINT",), "typed like production, not left as text"


def test_the_site_documents_every_gold_mart_with_its_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPOSITORY_ROOT)
    output = tmp_path / "site"

    assert docs_site.build(output, report=lambda line: None) == 0

    page = (output / "index.html").read_text(encoding="utf-8")
    for mart in ("dim_satellite", "fct_daily_decay", "fct_space_weather_daily"):
        assert mart in page
    assert '"BIGINT"' in page, "column types come from the catalog, so it was built"
