"""Contract for NASA OMNI hourly space-weather records.

Values are kept exactly as the server delivers them, including OMNI's habit of
reporting Kp multiplied by ten (Kp 9.0 arrives as ``90``). Converting it is the
transformation layer's job; bronze keeps what arrived.

Fill markers have already become ``None`` in ``clients.hapi`` -- see the note
there about why 999.9 must never reach the science as a number.
"""

from __future__ import annotations

from typing import Any, Final

import pandera.polars as pa
import polars as pl

SOURCE: Final = "nasa.omni"

FIELD_MAP: Final[dict[str, str]] = {
    "Time": "observed_at",
    "F10_INDEX1800": "f10_7_sfu",
    "KP1800": "kp_x10",
    "DST1800": "dst_nt",
    "AP_INDEX1800": "ap_nt",
}

BRONZE_COLUMNS: Final[tuple[str, ...]] = (*FIELD_MAP.values(), "epoch_date")

_HAPI_TIME_FORMAT: Final = "%Y-%m-%dT%H:%M:%S%.fZ"

schema: Final = pa.DataFrameSchema(
    {
        "observed_at": pa.Column(pl.Datetime, nullable=False),
        # F10.7 solar radio flux in solar flux units. The quiet-Sun floor is
        # around 64; the largest values on record are a few hundred.
        "f10_7_sfu": pa.Column(pl.Float64, pa.Check.between(50.0, 600.0), nullable=True),
        # Kp x10, so the 0-9 scale arrives as 0-90.
        "kp_x10": pa.Column(pl.Int32, pa.Check.between(0, 90), nullable=True),
        # Dst goes negative during a storm; -406 nT was reached in May 2024.
        "dst_nt": pa.Column(pl.Int32, pa.Check.between(-1000, 200), nullable=True),
        # Ap is bounded above at 400 by construction of the index.
        "ap_nt": pa.Column(pl.Int32, pa.Check.between(0, 400), nullable=True),
        "epoch_date": pa.Column(pl.Date, nullable=False),
    },
    strict=True,
    ordered=True,
    name="bronze_omni",
)


def to_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """Cast parsed HAPI records into the typed bronze frame, sorted by time."""
    if not rows:
        return pl.DataFrame(
            schema={
                "observed_at": pl.Datetime("us"),
                "f10_7_sfu": pl.Float64,
                "kp_x10": pl.Int32,
                "dst_nt": pl.Int32,
                "ap_nt": pl.Int32,
                "epoch_date": pl.Date,
            }
        )

    frame = pl.DataFrame(
        [{k: r.get(k) for k in FIELD_MAP} for r in rows],
        schema={
            "Time": pl.Utf8,
            "F10_INDEX1800": pl.Float64,
            "KP1800": pl.Float64,
            "DST1800": pl.Float64,
            "AP_INDEX1800": pl.Float64,
        },
        strict=False,
    ).rename(FIELD_MAP)

    frame = frame.with_columns(
        # HAPI stamps a literal trailing Z. Polars refuses to infer a format
        # when a zone is present, so it is given explicitly; the result is kept
        # naive-UTC to match Space-Track epochs, which carry no zone at all.
        pl.col("observed_at").str.to_datetime(
            format=_HAPI_TIME_FORMAT, strict=False, time_unit="us"
        ),
        pl.col("kp_x10").cast(pl.Int32, strict=False),
        pl.col("dst_nt").cast(pl.Int32, strict=False),
        pl.col("ap_nt").cast(pl.Int32, strict=False),
    )
    frame = frame.with_columns(pl.col("observed_at").dt.date().alias("epoch_date"))
    return frame.select(BRONZE_COLUMNS).sort("observed_at")
