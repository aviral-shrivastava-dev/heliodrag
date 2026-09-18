"""Expose bronze Iceberg tables to DuckDB, so dbt can read them.

DuckDB's ``iceberg_scan`` cannot read this lake. It needs a version hint that
dlt does not write, and it cannot open the ``file://C:/...`` URIs pyiceberg
emits on Windows -- two slashes rather than three, so the drive letter is parsed
as a hostname. ``allow_moved_paths`` does not rescue it.

What works, and is more correct anyway: ask pyiceberg for the files the current
snapshot references, and hand that list to ``read_parquet``. Going through the
snapshot excludes superseded files, which a directory glob would silently
include -- bronze is append-only, so a partition accumulates old copies.

The views are a materialised view of a moving target, so they are rebuilt
before each dbt run rather than created once. ``starlink-drag warehouse sync``
does that, and ``make build`` calls it first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from starlink_drag.config import Settings
from starlink_drag.ingest.bronze import (
    AUDIT_TABLE,
    GP_TABLE,
    OMNI_TABLE,
    QUARANTINE_TABLE,
    SATCAT_TABLE,
    iceberg_table,
    local_path,
)

BRONZE_SCHEMA = "bronze"

#: Bronze table -> the view name dbt sources refer to.
VIEWS: dict[str, str] = {
    GP_TABLE: "gp_history",
    OMNI_TABLE: "omni",
    SATCAT_TABLE: "satcat",
    QUARANTINE_TABLE: "quarantine",
    AUDIT_TABLE: "ingest_audit",
}


@dataclass(frozen=True, slots=True)
class ViewSync:
    """What one view ended up pointing at."""

    view: str
    files: int
    rows: int
    skipped_reason: str | None = None

    @property
    def created(self) -> bool:
        return self.skipped_reason is None


def live_files(settings: Settings, table: str) -> list[str]:
    """Paths of the Parquet files the table's current snapshot references."""
    handle = iceberg_table(settings, table)
    if handle is None:
        return []
    paths = []
    for task in handle.scan().plan_files():
        path = local_path(task.file.file_path)
        if path is not None and path.exists():
            paths.append(path.as_posix())
    return sorted(paths)


def sync(settings: Settings, *, database: Path | None = None) -> list[ViewSync]:
    """Rebuild every bronze view in the warehouse. Returns what each now holds."""
    target = database or settings.duckdb_path
    target.parent.mkdir(parents=True, exist_ok=True)

    results: list[ViewSync] = []
    with duckdb.connect(str(target)) as con:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {BRONZE_SCHEMA}")
        for table, view in VIEWS.items():
            qualified = f"{BRONZE_SCHEMA}.{view}"
            files = live_files(settings, table)
            if not files:
                con.execute(f"DROP VIEW IF EXISTS {qualified}")
                results.append(ViewSync(view, 0, 0, "no data landed yet"))
                continue

            # The file list is inlined rather than bound: DuckDB refuses a
            # prepared parameter inside CREATE VIEW, because the view has to
            # store a literal definition.
            con.execute(
                f"CREATE OR REPLACE VIEW {qualified} AS "
                f"SELECT * FROM read_parquet([{_sql_list(files)}])"
            )
            rows = con.execute(f"SELECT count(*) FROM {qualified}").fetchone()
            results.append(ViewSync(view, len(files), int(rows[0]) if rows else 0))
    return results


def describe(results: list[ViewSync]) -> str:
    lines = []
    for result in results:
        if result.created:
            lines.append(f"  {result.view:<14} {result.rows:>10,} rows  {result.files:>5} files")
        else:
            lines.append(f"  {result.view:<14} {'-':>10}        ({result.skipped_reason})")
    return "\n".join(lines)


def _sql_list(paths: list[str]) -> str:
    """Render paths as a SQL string list, escaping any embedded quote."""
    return ", ".join("'" + path.replace("'", "''") + "'" for path in paths)
