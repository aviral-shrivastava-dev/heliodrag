"""Land the Space-Track catalogue into bronze, and read back the object list.

SATCAT is a snapshot of current state rather than a time series, so it is
partitioned by ``ingest_date``: each run records a complete picture, and
comparing two runs is how a decay is detected.
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from starlink_drag.clients.spacetrack import SpaceTrackClient
from starlink_drag.config import Settings
from starlink_drag.ingest.bronze import (
    SATCAT_SPEC,
    LoadOutcome,
    land,
    read_table,
)
from starlink_drag.schemas import satcat

STARLINK_NAME_PATTERN = "~~STARLINK"


def ingest_satcat(
    settings: Settings,
    *,
    ingest_date: dt.date | None = None,
    client: SpaceTrackClient | None = None,
    name_pattern: str = STARLINK_NAME_PATTERN,
) -> LoadOutcome:
    """Land the current Starlink catalogue as one dated snapshot."""
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    owned = client is None
    spacetrack = client or SpaceTrackClient(settings.spacetrack)
    try:
        rows = spacetrack.satcat(name_pattern=name_pattern)
    finally:
        if owned:
            spacetrack.close()

    frame = satcat.to_frame(rows, ingest_date)
    return land(frame, SATCAT_SPEC, settings, schema=satcat.schema, source=satcat.SOURCE)


def norad_ids(
    settings: Settings,
    *,
    on_orbit_during: tuple[dt.date, dt.date] | None = None,
) -> list[int]:
    """NORAD IDs from the most recent SATCAT snapshot in bronze.

    ``on_orbit_during`` drops objects that had not launched by the end of the
    window or had already decayed before its start. Fetching elements for a
    satellite that did not exist yet wastes a request against a hard rate limit.
    """
    # Through the table's current snapshot, never a directory listing, which
    # would work only for a local lake and would include superseded files.
    frame = read_table(settings, satcat_table_name())
    if frame.is_empty():
        raise FileNotFoundError(
            "no SATCAT snapshot in bronze. Run `starlink-drag ingest satcat` first."
        )

    latest = frame["ingest_date"].max()
    frame = frame.filter(pl.col("ingest_date") == latest)

    if on_orbit_during is not None:
        start, end = on_orbit_during
        frame = frame.filter(
            (pl.col("launch_date").is_null() | (pl.col("launch_date") < end))
            & (pl.col("decay_date").is_null() | (pl.col("decay_date") >= start))
        )

    ids: list[int] = frame["norad_id"].unique().sort().to_list()
    return ids


def satcat_table_name() -> str:
    return SATCAT_SPEC.table
