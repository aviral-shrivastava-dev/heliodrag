"""Bronze landing zone: partitioned Iceberg tables written through dlt.

Three properties this module exists to guarantee.

**Partition-scoped idempotency.** Each table is partitioned by a date column and
loaded with a merge/upsert keyed on that partition plus the natural key. Loading
``epoch_date=2024-05-10`` again rewrites that partition and leaves every other
one untouched.

**Byte-identical re-runs.** Resources yield Arrow tables, for which dlt's
parquet normaliser adds neither ``_dlt_id`` nor ``_dlt_load_id`` -- both would
be random or clock-derived and would defeat this. Rows are sorted on a stable
key before writing, and no wall-clock value is stored in a data column. Two runs
over the same input therefore produce identical Parquet bytes.

**Provenance without poisoning the bytes.** ``ingest_timestamp`` is what makes
"immutable and append-only" and "byte-identical" contradictory, so it lives in a
separate ``bronze_ingest_audit`` table rather than in the data rows. Iceberg's
snapshot history supplies the rest: nothing is ever mutated in place. See
ADR-0004.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import unquote, urlparse

import dlt
import polars as pl
from pyiceberg.table import StaticTable

from starlink_drag.config import Settings

GP_TABLE: Final = "bronze_gp_history"
OMNI_TABLE: Final = "bronze_omni"
SATCAT_TABLE: Final = "bronze_satcat"
QUARANTINE_TABLE: Final = "bronze_quarantine"
AUDIT_TABLE: Final = "bronze_ingest_audit"

DATASET: Final = "bronze"

_METADATA_VERSION: Final = re.compile(r"^(\d+)-")
_WINDOWS_DRIVE: Final = re.compile(r"[A-Za-z]:")
_LEADING_DRIVE: Final = re.compile(r"^/[A-Za-z]:")


@dataclass(frozen=True, slots=True)
class BronzeSpec:
    """How one bronze table is keyed and partitioned."""

    table: str
    partition_column: str
    primary_key: tuple[str, ...]


GP_SPEC: Final = BronzeSpec(GP_TABLE, "epoch_date", ("gp_id",))
OMNI_SPEC: Final = BronzeSpec(OMNI_TABLE, "epoch_date", ("observed_at",))
SATCAT_SPEC: Final = BronzeSpec(SATCAT_TABLE, "ingest_date", ("ingest_date", "norad_id"))


@dataclass(frozen=True, slots=True)
class LoadOutcome:
    """What a single bronze load did."""

    table: str
    partitions: tuple[str, ...]
    rows_written: int
    rows_quarantined: int

    def describe(self) -> str:
        span = (
            f"{self.partitions[0]}..{self.partitions[-1]}"
            if len(self.partitions) > 1
            else (self.partitions[0] if self.partitions else "none")
        )
        return (
            f"{self.table}: {self.rows_written:,} rows over "
            f"{len(self.partitions)} partition(s) [{span}]"
            + (f", {self.rows_quarantined:,} quarantined" if self.rows_quarantined else "")
        )


def lake_root(settings: Settings) -> str:
    """Where bronze lives: a local directory, or the S3-compatible bucket."""
    if settings.lake.backend == "local":
        return str((settings.data_dir / "lake").resolve())
    return f"s3://{settings.lake.bucket}"


def pipelines_dir(settings: Settings) -> Path:
    """dlt's own working directory.

    Deliberately kept inside ``data/`` and shallow: dlt's state filenames are
    around a hundred characters, and a deep parent breaks Windows' 260-character
    path limit with a bare FileNotFoundError.
    """
    path = settings.data_dir / ".dlt"
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_pipeline(name: str, settings: Settings) -> dlt.Pipeline:
    return dlt.pipeline(
        pipeline_name=name,
        destination=dlt.destinations.filesystem(lake_root(settings)),
        dataset_name=DATASET,
        pipelines_dir=str(pipelines_dir(settings)),
    )


def write(
    frame: pl.DataFrame,
    spec: BronzeSpec,
    settings: Settings,
    *,
    pipeline_name: str | None = None,
) -> LoadOutcome:
    """Load a typed frame into its bronze table, replacing whole partitions."""
    partitions = _partition_values(frame, spec.partition_column)
    if frame.is_empty():
        return LoadOutcome(spec.table, (), 0, 0)

    resource = dlt.resource(
        frame.to_arrow(),
        name=spec.table,
        write_disposition={"disposition": "merge", "strategy": "upsert"},
        primary_key=list(spec.primary_key),
        columns={spec.partition_column: {"partition": True}},
    )
    pipeline = make_pipeline(pipeline_name or spec.table, settings)
    pipeline.run(resource, table_format="iceberg", loader_file_format="parquet")
    return LoadOutcome(spec.table, partitions, frame.height, 0)


def write_quarantine(frame: pl.DataFrame, settings: Settings) -> int:
    """Append failed rows to the quarantine table.

    Quarantine is append-only and deliberately not deduplicated: the point is to
    see every time a contract was violated, not the latest state.
    """
    if frame.is_empty():
        return 0
    resource = dlt.resource(
        frame.to_arrow(),
        name=QUARANTINE_TABLE,
        write_disposition="append",
        columns={"ingest_date": {"partition": True}},
    )
    pipeline = make_pipeline(QUARANTINE_TABLE, settings)
    pipeline.run(resource, table_format="iceberg", loader_file_format="parquet")
    return frame.height


def write_audit(
    settings: Settings,
    *,
    table: str,
    partitions: tuple[str, ...],
    rows_written: int,
    rows_quarantined: int,
    source: str,
) -> None:
    """Record when a load happened, outside the data files.

    This is the home of ``ingest_timestamp``. Keeping it here rather than in a
    bronze column is what lets the data files stay byte-identical across runs
    while provenance is still fully recorded.
    """
    now = dt.datetime.now(dt.UTC)
    audit = pl.DataFrame(
        {
            "table_name": [table],
            "source": [source],
            "partition_from": [partitions[0] if partitions else None],
            "partition_to": [partitions[-1] if partitions else None],
            "partition_count": [len(partitions)],
            "rows_written": [rows_written],
            "rows_quarantined": [rows_quarantined],
            "ingest_timestamp": [now.replace(tzinfo=None)],
            "ingest_date": [now.date()],
        },
        schema={
            "table_name": pl.Utf8,
            "source": pl.Utf8,
            "partition_from": pl.Utf8,
            "partition_to": pl.Utf8,
            "partition_count": pl.Int64,
            "rows_written": pl.Int64,
            "rows_quarantined": pl.Int64,
            "ingest_timestamp": pl.Datetime("us"),
            "ingest_date": pl.Date,
        },
    )
    resource = dlt.resource(
        audit.to_arrow(),
        name=AUDIT_TABLE,
        write_disposition="append",
        columns={"ingest_date": {"partition": True}},
    )
    make_pipeline(AUDIT_TABLE, settings).run(
        resource, table_format="iceberg", loader_file_format="parquet"
    )


def land(
    frame: pl.DataFrame,
    spec: BronzeSpec,
    settings: Settings,
    *,
    schema: Any,
    source: str,
) -> LoadOutcome:
    """Validate, then land the good rows and quarantine the bad ones.

    A batch is never dropped wholesale for one bad record, and a bad record is
    never silently discarded. Both halves are written before the audit row, so
    an interrupted load leaves no audit claiming success.
    """
    from starlink_drag.schemas.validate import validate

    outcome = validate(frame, schema, source=source)
    loaded = write(outcome.valid, spec, settings)
    quarantined = write_quarantine(outcome.quarantined, settings)
    write_audit(
        settings,
        table=spec.table,
        partitions=loaded.partitions,
        rows_written=loaded.rows_written,
        rows_quarantined=quarantined,
        source=source,
    )
    return LoadOutcome(spec.table, loaded.partitions, loaded.rows_written, quarantined)


def iceberg_table(settings: Settings, table: str) -> StaticTable | None:
    """Open a bronze table at its current snapshot, or ``None`` if absent.

    Reading a bronze table means reading the snapshot, never the directory.
    Iceberg does not delete a superseded data file when a partition is
    replaced -- it stops referencing it, and the old file stays on disk until
    snapshots are expired. Globbing ``data/`` therefore returns rows that the
    table no longer contains, silently mixing a partition's old and new
    contents.
    """
    metadata_dir = Path(lake_root(settings)) / DATASET / table / "metadata"
    if not metadata_dir.exists():
        return None
    versions = [
        (int(match.group(1)), path)
        for path in metadata_dir.glob("*.metadata.json")
        if (match := _METADATA_VERSION.match(path.name))
    ]
    if not versions:
        return None
    _, latest = max(versions)
    return StaticTable.from_metadata(str(latest))


def read_table(settings: Settings, table: str, *, row_filter: str = "true") -> pl.DataFrame:
    """Read a bronze table's current snapshot into a Polars frame."""
    handle = iceberg_table(settings, table)
    if handle is None:
        return pl.DataFrame()
    return pl.from_arrow(handle.scan(row_filter=row_filter).to_arrow())  # type: ignore[return-value]


def partition_digest(
    settings: Settings, table: str, partition_column: str, value: str
) -> list[str]:
    """SHA-256 of the data files the current snapshot holds for one partition.

    This is how the byte-identity guarantee is checked: load a partition twice,
    compare the digests. Only files the snapshot actually references are
    hashed -- see :func:`iceberg_table` for why the directory listing would
    give a wrong answer.
    """
    handle = iceberg_table(settings, table)
    if handle is None:
        return []
    scan = handle.scan(row_filter=f"{partition_column} = '{value}'")
    digests = []
    for task in scan.plan_files():
        path = local_path(task.file.file_path)
        if path is not None and path.exists():
            digests.append(hashlib.sha256(path.read_bytes()).hexdigest())
    return sorted(digests)


def local_path(uri: str) -> Path | None:
    """Convert an Iceberg data-file URI to a local path, or ``None`` if remote.

    On Windows, Iceberg writes ``file://C:/dir/x.parquet`` with two slashes
    rather than three. ``urlparse`` then reads ``C:`` as the *host* and hands
    back a path with the drive letter missing, which silently resolves to a
    file that does not exist. The drive is put back here.
    """
    parsed = urlparse(uri)
    if parsed.scheme not in ("", "file"):
        return None  # an object-store lake; nothing to hash locally
    path = unquote(parsed.path)
    if parsed.netloc and _WINDOWS_DRIVE.fullmatch(parsed.netloc):
        return Path(f"{parsed.netloc}{path}")
    if _LEADING_DRIVE.match(path):
        path = path[1:]
    return Path(path)


def _partition_values(frame: pl.DataFrame, column: str) -> tuple[str, ...]:
    if frame.is_empty() or column not in frame.columns:
        return ()
    values: list[Any] = frame[column].unique().sort().to_list()
    return tuple(str(v) for v in values)
