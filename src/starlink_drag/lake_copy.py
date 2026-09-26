"""Copy the local lake into an S3 lake, with no API calls.

The full 2020-to-present history took hours of rate-limited Space-Track
requests to land. Moving it to object storage -- the Docker stack's SeaweedFS,
or Cloudflare R2 -- should not repeat any of them. This reads each bronze table
through its current snapshot and appends it to the target through the same
writer the pipeline uses, so the copy is an ordinary lake the pipeline can read
and extend.

Four properties it keeps:

- **Bounded memory.** Element sets are copied ten days at a time, about
  200,000 rows; the small tables in one go. A calendar month at a time was
  tried first, and on 2026-09-26 the laptop running it -- with Docker's VM and
  a browser open -- hit Windows' virtual-memory limit and Docker's VM was reset
  mid-copy.
- **Resumable.** Before the element sets are copied, both lakes' rows per day
  are counted from Iceberg metadata, once. A chunk the target holds in full is
  skipped, an empty one copied, and anything in between stops the copy. An
  Iceberg commit is all-or-nothing, so a crash cannot leave half a chunk, and a
  copy that died can simply be run again.
- **Verified, not assumed.** After each table, the target snapshot's row count
  must equal the source's, or the copy fails.
- **Isolated state.** dlt keeps per-pipeline state on disk, keyed by table
  name. The copy uses its own working directory, so it cannot disturb the local
  pipeline's.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final

import pyarrow

from starlink_drag import lake
from starlink_drag.config import Settings
from starlink_drag.ingest import bronze

QUARANTINE_SPEC: Final = bronze.BronzeSpec(bronze.QUARANTINE_TABLE, "ingest_date", ())
AUDIT_SPEC: Final = bronze.BronzeSpec(bronze.AUDIT_TABLE, "ingest_date", ())

#: Copied in chunks: element sets, about 30M rows.
CHUNKED: Final = (bronze.GP_SPEC,)
#: Small enough to copy in one go: hourly space weather (about 100,000 rows),
#: and one row per catalogue entry, validation failure or load.
WHOLE: Final = (bronze.OMNI_SPEC, bronze.SATCAT_SPEC, QUARANTINE_SPEC, AUDIT_SPEC)


@dataclass(frozen=True, slots=True)
class TableCopy:
    table: str
    source_rows: int
    target_rows: int

    @property
    def verified(self) -> bool:
        return self.source_rows == self.target_rows

    def describe(self) -> str:
        mark = "ok" if self.verified else "MISMATCH"
        return f"  {self.table:<22} {self.source_rows:>12,} -> {self.target_rows:>12,}  {mark}"


CHUNK_DAYS: Final = 10


def chunks(
    start: dt.date, end: dt.date, days: int = CHUNK_DAYS
) -> Iterator[tuple[dt.date, dt.date]]:
    """Consecutive half-open ranges of ``days`` covering ``[start, end)``."""
    if days < 1:
        raise ValueError("a chunk must be at least one day")
    cursor = start
    while cursor < end:
        following = min(cursor + dt.timedelta(days=days), end)
        yield cursor, following
        cursor = following


EPOCH: Final = dt.date(1970, 1, 1)


def rows_by_day(settings: Settings, table: str) -> dict[dt.date, int]:
    """Rows per daily partition, from one pass over the table's metadata.

    Read once per copy rather than once per chunk: asking the target about each
    chunk re-read every manifest written so far, which made the copy quadratic
    in time and grew its memory by about 4 GB over 228 chunks -- until the
    memory watchdog stopped it.
    """
    handle = bronze.iceberg_table(settings, table)
    if handle is None:
        return {}
    counts: dict[dt.date, int] = {}
    for task in handle.scan().plan_files():
        # An identity-partitioned date is stored as days since 1970-01-01.
        day = EPOCH + dt.timedelta(days=int(task.file.partition[0]))
        counts[day] = counts.get(day, 0) + task.file.record_count
    return counts


def rows_in(settings: Settings, table: str, row_filter: str = "true") -> int:
    """Rows matching ``row_filter``, counted from Iceberg metadata -- no scan.

    Exact for filters on the partition column, which is all it is used for:
    whole files either match or do not.
    """
    handle = bronze.iceberg_table(settings, table)
    if handle is None:
        return 0
    return sum(task.file.record_count for task in handle.scan(row_filter=row_filter).plan_files())


def total_rows(settings: Settings, table: str) -> int:
    """Rows in the table's current snapshot, from its metadata -- no scan."""
    handle = bronze.iceberg_table(settings, table)
    snapshot = handle.current_snapshot() if handle else None
    if snapshot is None or snapshot.summary is None:
        return 0
    return int(snapshot.summary.get("total-records", 0))


def copy_lake(
    source: Settings,
    target: Settings,
    *,
    until: dt.date | None = None,
    report: Callable[[str], None] = print,
) -> list[TableCopy]:
    """Copy every bronze table from ``source`` to ``target``, then verify counts.

    Safe to run again after a failure: chunks the target already holds in full
    are skipped.
    """
    if lake.is_remote(source) or not lake.is_remote(target):
        raise ValueError("copy_lake copies a local lake into a remote one")
    for spec in (*CHUNKED, *WHOLE):
        # A crashed copy can leave a dlt package pending. Loading it would
        # duplicate a chunk that is about to be re-read from the source anyway.
        pipeline = bronze.make_pipeline(spec.table, target)
        if pipeline.has_pending_data:
            pipeline.abort_packages()

    last = until or dt.date.today() + dt.timedelta(days=1)
    results = []
    for spec in CHUNKED:
        have = rows_by_day(source, spec.table)
        done = rows_by_day(target, spec.table)
        for chunk_start, chunk_end in chunks(source.pipeline_start_date, last):
            days = [d for d in have if chunk_start <= d < chunk_end]
            wanted = sum(have[d] for d in days)
            present = sum(done.get(d, 0) for d in days)
            label = f"{chunk_start}..{chunk_end}"
            if wanted == 0 or present == wanted:
                continue
            if present:
                raise RuntimeError(
                    f"{spec.table} {label}: the target holds {present:,} of {wanted:,} rows. "
                    "An Iceberg commit is all-or-nothing, so this is not a crashed chunk; "
                    "empty the bucket and copy again."
                )
            row_filter = (
                f"{spec.partition_column} >= '{chunk_start}' and "
                f"{spec.partition_column} < '{chunk_end}'"
            )
            frame = bronze.read_table(source, spec.table, row_filter=row_filter)
            bronze.write(frame, spec, target)
            report(f"  {spec.table} {label}: {frame.height:,} rows")
            del frame
            pyarrow.default_memory_pool().release_unused()
        results.append(_verify(source, target, spec.table, report))

    for spec in WHOLE:
        _copy_range(source, target, spec, "true", "all", report)
        results.append(_verify(source, target, spec.table, report))
    return results


def _copy_range(
    source: Settings,
    target: Settings,
    spec: bronze.BronzeSpec,
    row_filter: str,
    label: str,
    report: Callable[[str], None],
) -> None:
    wanted = rows_in(source, spec.table, row_filter)
    if wanted == 0:
        return  # nothing to copy, so no need to ask the (remote, slower) target
    present = rows_in(target, spec.table, row_filter)
    if present == wanted:
        return
    if present:
        raise RuntimeError(
            f"{spec.table} {label}: the target holds {present:,} of {wanted:,} rows. "
            "An Iceberg commit is all-or-nothing, so this is not a crashed chunk; "
            "empty the bucket and copy again."
        )
    frame = bronze.read_table(source, spec.table, row_filter=row_filter)
    bronze.write(frame, spec, target)
    report(f"  {spec.table} {label}: {frame.height:,} rows")


def _verify(
    source: Settings, target: Settings, table: str, report: Callable[[str], None]
) -> TableCopy:
    result = TableCopy(table, total_rows(source, table), total_rows(target, table))
    report(result.describe())
    if not result.verified:
        raise RuntimeError(
            f"{table}: {result.source_rows:,} rows in the source but "
            f"{result.target_rows:,} in the target"
        )
    return result
