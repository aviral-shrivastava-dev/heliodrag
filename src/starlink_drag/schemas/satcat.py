"""Contract for Space-Track SATCAT records.

SATCAT is a snapshot of the catalogue's *current* state, not a time series: a
satellite's row changes when it decays. It is therefore partitioned by
``ingest_date`` rather than by an epoch, and each ingest is a new, complete
picture. Comparing two ingests is how decay events are detected.

This supplies the launch date and decay date that Phase 2 uses to label hardware
generations and to exclude satellites that are still raising their orbits.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final

import pandera.polars as pa
import polars as pl

SOURCE: Final = "spacetrack.satcat"

FIELD_MAP: Final[dict[str, str]] = {
    "NORAD_CAT_ID": "norad_id",
    "INTLDES": "international_designator",
    "SATNAME": "object_name",
    "OBJECT_TYPE": "object_type",
    "COUNTRY": "country",
    "LAUNCH": "launch_date",
    "DECAY": "decay_date",
    "LAUNCH_YEAR": "launch_year",
    "LAUNCH_NUM": "launch_number",
    "LAUNCH_PIECE": "launch_piece",
    "SITE": "site",
    "PERIOD": "period_minutes",
    "INCLINATION": "inclination",
    "APOGEE": "apogee_km",
    "PERIGEE": "perigee_km",
    "RCS_SIZE": "rcs_size",
    "RCSVALUE": "rcs_value",
    "CURRENT": "is_current",
}

BRONZE_COLUMNS: Final[tuple[str, ...]] = (*FIELD_MAP.values(), "ingest_date")

_INTEGERS: Final = ("norad_id", "launch_year", "launch_number", "rcs_value")
_FLOATS: Final = ("period_minutes", "inclination", "apogee_km", "perigee_km")
_DATES: Final = ("launch_date", "decay_date")

schema: Final = pa.DataFrameSchema(
    {
        "norad_id": pa.Column(pl.Int64, pa.Check.gt(0)),
        "international_designator": pa.Column(pl.Utf8, nullable=True),
        "object_name": pa.Column(pl.Utf8, nullable=True),
        "object_type": pa.Column(pl.Utf8, nullable=True),
        "country": pa.Column(pl.Utf8, nullable=True),
        # Starlink began in 2019; nothing predates the space age.
        "launch_date": pa.Column(pl.Date, nullable=True),
        "decay_date": pa.Column(pl.Date, nullable=True),
        "launch_year": pa.Column(pl.Int64, pa.Check.between(1957, 2100), nullable=True),
        "launch_number": pa.Column(pl.Int64, nullable=True),
        "launch_piece": pa.Column(pl.Utf8, nullable=True),
        "site": pa.Column(pl.Utf8, nullable=True),
        "period_minutes": pa.Column(pl.Float64, pa.Check.gt(0.0), nullable=True),
        "inclination": pa.Column(pl.Float64, pa.Check.between(0.0, 180.0), nullable=True),
        "apogee_km": pa.Column(pl.Float64, nullable=True),
        "perigee_km": pa.Column(pl.Float64, nullable=True),
        "rcs_size": pa.Column(pl.Utf8, nullable=True),
        "rcs_value": pa.Column(pl.Int64, nullable=True),
        "is_current": pa.Column(pl.Utf8, nullable=True),
        "ingest_date": pa.Column(pl.Date, nullable=False),
    },
    strict=True,
    ordered=True,
    name="bronze_satcat",
)


def to_frame(rows: list[dict[str, Any]], ingest_date: dt.date) -> pl.DataFrame:
    """Cast raw SATCAT records into the typed bronze frame, sorted by NORAD ID."""
    if not rows:
        # Typed from the contract, not left as strings: an empty array is what a
        # throttled Space-Track returns, and the frame still has to match the
        # bronze table it would be written to.
        return pl.DataFrame(schema={c: _polars_dtype(c) for c in BRONZE_COLUMNS})

    frame = pl.DataFrame(
        [{k: r.get(k) for k in FIELD_MAP} for r in rows],
        schema={k: pl.Utf8 for k in FIELD_MAP},
        strict=False,
    ).rename(FIELD_MAP)

    frame = frame.with_columns(
        *(pl.col(c).cast(pl.Int64, strict=False) for c in _INTEGERS),
        *(pl.col(c).cast(pl.Float64, strict=False) for c in _FLOATS),
        *(pl.col(c).str.to_date(strict=False) for c in _DATES),
    )
    frame = frame.with_columns(pl.lit(ingest_date).cast(pl.Date).alias("ingest_date"))
    return frame.select(BRONZE_COLUMNS).sort("norad_id")


def _polars_dtype(column: str) -> pl.DataType:
    dtype = schema.columns[column].dtype
    return dtype.type if hasattr(dtype, "type") else dtype  # type: ignore[no-any-return]
