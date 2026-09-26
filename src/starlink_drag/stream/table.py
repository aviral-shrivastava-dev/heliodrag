"""The nowcast's own Iceberg table: ``stream/stream_drag_nowcast``.

A separate dataset from ``bronze``, so nothing the batch path reads or writes
can see it, and the warehouse views never include it. Written through the same
dlt path as bronze -- ``bronze.run_pipeline``, unchanged, so a crashed write's
pending package is finished rather than silently dropped -- and read, like
bronze, through the current snapshot and never the directory.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path, PurePosixPath
from typing import Final

import dlt
import polars as pl
from pyiceberg.table import StaticTable

from starlink_drag import lake
from starlink_drag.config import Settings
from starlink_drag.ingest import bronze
from starlink_drag.stream.nowcast import NOWCAST_SCHEMA

DATASET: Final = "stream"
NOWCAST_TABLE: Final = "stream_drag_nowcast"
PARTITION: Final = "computed_date"

_METADATA_VERSION: Final = re.compile(r"^(\d+)-")


def write_nowcast(frame: pl.DataFrame, settings: Settings) -> int:
    """Append one snapshot. Returns the rows written."""
    if frame.is_empty():
        return 0
    pipeline = dlt.pipeline(
        pipeline_name=NOWCAST_TABLE,
        destination=lake.dlt_destination(settings),
        dataset_name=DATASET,
        pipelines_dir=str(bronze.pipelines_dir(settings)),
    )
    resource = dlt.resource(
        frame.to_arrow(),
        name=NOWCAST_TABLE,
        write_disposition="append",
        columns={PARTITION: {"partition": True}},
    )
    bronze.run_pipeline(pipeline, resource)
    return frame.height


def read_nowcast(settings: Settings, *, since: dt.date | None = None) -> pl.DataFrame:
    """Snapshots computed on or after ``since`` (all of them when ``None``)."""
    handle = _table(settings)
    if handle is None:
        return pl.DataFrame(schema=NOWCAST_SCHEMA)
    row_filter = f"{PARTITION} >= '{since.isoformat()}'" if since else "true"
    frame = pl.from_arrow(handle.scan(row_filter=row_filter).to_arrow())
    assert isinstance(frame, pl.DataFrame)
    # dlt adds its own load columns; the schema is what the nowcast defines.
    return frame.select([pl.col(name).cast(dtype) for name, dtype in NOWCAST_SCHEMA.items()])


def latest(frame: pl.DataFrame) -> pl.DataFrame:
    """Each generation's most recent snapshot row."""
    if frame.is_empty():
        return frame
    return (
        frame.sort("computed_at")
        .group_by("generation", maintain_order=True)
        .last()
        .sort("generation")
    )


def _table(settings: Settings) -> StaticTable | None:
    metadata_dir = f"{lake.root(settings)}/{DATASET}/{NOWCAST_TABLE}/metadata"
    if lake.is_remote(settings):
        listing = [
            f"s3://{path}"
            for path in lake.filesystem(settings).glob(f"{metadata_dir}/*.metadata.json")
        ]
    else:
        local = Path(metadata_dir)
        listing = [str(path) for path in local.glob("*.metadata.json")] if local.exists() else []
    versions = [
        (int(match.group(1)), path)
        for path in listing
        if (match := _METADATA_VERSION.match(PurePosixPath(path.replace("\\", "/")).name))
    ]
    if not versions:
        return None
    _, newest = max(versions)
    return StaticTable.from_metadata(newest, properties=lake.iceberg_properties(settings))
