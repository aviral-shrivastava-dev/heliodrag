"""Contract for Space-Track ``gp_history`` records.

Space-Track returns every field as a JSON string, including numbers: mean motion
arrives as ``"15.06402759"`` and the NORAD ID as ``"44713"``. Casting therefore
happens here, at the boundary, and the typed frame is what bronze stores.

The whole record is kept, including the raw TLE lines. They are redundant with
the parsed elements, but they are the authoritative artefact a reviewer can
re-derive everything from, and bronze exists to be the thing you can go back to.

Bounds are deliberately generous. A contract that quarantines real data because
a satellite is decaying unusually fast is worse than no contract -- these reject
impossible values, not merely surprising ones.
"""

from __future__ import annotations

from typing import Any, Final

import pandera.polars as pa
import polars as pl

SOURCE: Final = "spacetrack.gp_history"

#: Raw Space-Track field -> bronze column. Ordering is fixed and is part of the
#: byte-identity contract: the same rows must always produce the same columns
#: in the same order.
FIELD_MAP: Final[dict[str, str]] = {
    "GP_ID": "gp_id",
    "NORAD_CAT_ID": "norad_id",
    "OBJECT_NAME": "object_name",
    "OBJECT_ID": "object_id",
    "OBJECT_TYPE": "object_type",
    "EPOCH": "epoch",
    "MEAN_MOTION": "mean_motion",
    "ECCENTRICITY": "eccentricity",
    "INCLINATION": "inclination",
    "RA_OF_ASC_NODE": "ra_of_asc_node",
    "ARG_OF_PERICENTER": "arg_of_pericenter",
    "MEAN_ANOMALY": "mean_anomaly",
    "BSTAR": "bstar",
    "MEAN_MOTION_DOT": "mean_motion_dot",
    "MEAN_MOTION_DDOT": "mean_motion_ddot",
    "SEMIMAJOR_AXIS": "semimajor_axis_km",
    "PERIOD": "period_minutes",
    "APOAPSIS": "apoapsis_km",
    "PERIAPSIS": "periapsis_km",
    "REV_AT_EPOCH": "rev_at_epoch",
    "ELEMENT_SET_NO": "element_set_no",
    "EPHEMERIS_TYPE": "ephemeris_type",
    "CLASSIFICATION_TYPE": "classification_type",
    "RCS_SIZE": "rcs_size",
    "COUNTRY_CODE": "country_code",
    "SITE": "site",
    "LAUNCH_DATE": "launch_date",
    "DECAY_DATE": "decay_date",
    "CREATION_DATE": "creation_date",
    "FILE": "file_id",
    "TLE_LINE0": "tle_line0",
    "TLE_LINE1": "tle_line1",
    "TLE_LINE2": "tle_line2",
}

_INTEGERS: Final = ("gp_id", "norad_id", "rev_at_epoch", "element_set_no", "file_id")
_SMALL_INTEGERS: Final = ("ephemeris_type",)
_FLOATS: Final = (
    "mean_motion",
    "eccentricity",
    "inclination",
    "ra_of_asc_node",
    "arg_of_pericenter",
    "mean_anomaly",
    "bstar",
    "mean_motion_dot",
    "mean_motion_ddot",
    "semimajor_axis_km",
    "period_minutes",
    "apoapsis_km",
    "periapsis_km",
)
_DATES: Final = ("launch_date", "decay_date")
_TIMESTAMPS: Final = ("epoch", "creation_date")

#: Column order of the bronze table, with the partition key last.
BRONZE_COLUMNS: Final[tuple[str, ...]] = (*FIELD_MAP.values(), "epoch_date")

EARTH_RADIUS_KM: Final = 6378.137

schema: Final = pa.DataFrameSchema(
    {
        "gp_id": pa.Column(pl.Int64, pa.Check.gt(0)),
        "norad_id": pa.Column(pl.Int64, pa.Check.gt(0)),
        "object_name": pa.Column(pl.Utf8, nullable=True),
        "object_id": pa.Column(pl.Utf8, nullable=True),
        "object_type": pa.Column(pl.Utf8, nullable=True),
        "epoch": pa.Column(pl.Datetime, nullable=False),
        # Below ~0.5 rev/day is beyond geostationary; above 20 is below the
        # atmosphere. Starlink sits near 15.
        "mean_motion": pa.Column(pl.Float64, pa.Check.between(0.5, 20.0)),
        "eccentricity": pa.Column(pl.Float64, pa.Check.between(0.0, 1.0)),
        "inclination": pa.Column(pl.Float64, pa.Check.between(0.0, 180.0)),
        "ra_of_asc_node": pa.Column(pl.Float64, pa.Check.between(0.0, 360.0)),
        "arg_of_pericenter": pa.Column(pl.Float64, pa.Check.between(0.0, 360.0)),
        "mean_anomaly": pa.Column(pl.Float64, pa.Check.between(0.0, 360.0)),
        # BSTAR is signed: negative values occur and are physically meaningful
        # for objects whose fitted drag term absorbs manoeuvres.
        "bstar": pa.Column(pl.Float64, nullable=True),
        "mean_motion_dot": pa.Column(pl.Float64, nullable=True),
        "mean_motion_ddot": pa.Column(pl.Float64, nullable=True),
        # A semi-major axis is a radius, so it cannot be inside the Earth.
        "semimajor_axis_km": pa.Column(pl.Float64, pa.Check.gt(EARTH_RADIUS_KM), nullable=True),
        "period_minutes": pa.Column(pl.Float64, pa.Check.gt(0.0), nullable=True),
        # Apsides are altitudes above the surface, not radii.
        "apoapsis_km": pa.Column(pl.Float64, pa.Check.between(-100.0, 1e6), nullable=True),
        "periapsis_km": pa.Column(pl.Float64, pa.Check.between(-100.0, 1e6), nullable=True),
        "rev_at_epoch": pa.Column(pl.Int64, nullable=True),
        "element_set_no": pa.Column(pl.Int64, nullable=True),
        "ephemeris_type": pa.Column(pl.Int32, nullable=True),
        "classification_type": pa.Column(pl.Utf8, nullable=True),
        "rcs_size": pa.Column(pl.Utf8, nullable=True),
        "country_code": pa.Column(pl.Utf8, nullable=True),
        "site": pa.Column(pl.Utf8, nullable=True),
        "launch_date": pa.Column(pl.Date, nullable=True),
        "decay_date": pa.Column(pl.Date, nullable=True),
        "creation_date": pa.Column(pl.Datetime, nullable=True),
        "file_id": pa.Column(pl.Int64, nullable=True),
        "tle_line0": pa.Column(pl.Utf8, nullable=True),
        "tle_line1": pa.Column(pl.Utf8, nullable=True),
        "tle_line2": pa.Column(pl.Utf8, nullable=True),
        "epoch_date": pa.Column(pl.Date, nullable=False),
    },
    strict=True,
    ordered=True,
    name="bronze_gp_history",
)


def to_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """Cast raw Space-Track records into the typed bronze frame.

    Rows are sorted by ``(norad_id, epoch, gp_id)``. Space-Track does not
    guarantee a stable order between identical queries, and an unstable row
    order would make byte-identical re-runs impossible.
    """
    if not rows:
        return pl.DataFrame(schema={c: _polars_dtype(c) for c in BRONZE_COLUMNS})

    raw = pl.DataFrame(
        [{k: r.get(k) for k in FIELD_MAP} for r in rows],
        schema={k: pl.Utf8 for k in FIELD_MAP},
        strict=False,
    ).rename(FIELD_MAP)

    casts = [
        *(pl.col(c).cast(pl.Int64, strict=False) for c in _INTEGERS),
        *(pl.col(c).cast(pl.Int32, strict=False) for c in _SMALL_INTEGERS),
        *(pl.col(c).cast(pl.Float64, strict=False) for c in _FLOATS),
        *(pl.col(c).str.to_datetime(strict=False, time_unit="us") for c in _TIMESTAMPS),
        *(pl.col(c).str.to_date(strict=False) for c in _DATES),
    ]
    frame = raw.with_columns(casts)
    frame = frame.with_columns(pl.col("epoch").dt.date().alias("epoch_date"))
    return frame.select(BRONZE_COLUMNS).sort(["norad_id", "epoch", "gp_id"])


def _polars_dtype(column: str) -> pl.DataType:
    dtype = schema.columns[column].dtype
    return dtype.type if hasattr(dtype, "type") else dtype  # type: ignore[no-any-return]
