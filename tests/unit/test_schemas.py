"""Casting and validation at the ingestion boundary.

Run against `tests/fixtures/spacetrack`, which carries the real response
structure with substituted values -- see that directory's README for why.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from starlink_drag.schemas import gp, omni, satcat
from starlink_drag.schemas.validate import validate

FIXTURES = Path(__file__).parent.parent / "fixtures" / "spacetrack"


def _gp_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(
        (FIXTURES / "gp_history_sample.json").read_text(encoding="utf-8")
    )
    return rows


def _satcat_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(
        (FIXTURES / "satcat_sample.json").read_text(encoding="utf-8")
    )
    return rows


# -- gp casting ------------------------------------------------------------


def test_every_field_arrives_as_a_string_and_is_cast() -> None:
    """Space-Track returns numbers as JSON strings; bronze stores them typed."""
    raw = _gp_rows()[0]
    assert isinstance(raw["MEAN_MOTION"], str)
    assert isinstance(raw["NORAD_CAT_ID"], str)

    frame = gp.to_frame(_gp_rows())

    assert frame.schema["norad_id"] == pl.Int64
    assert frame.schema["mean_motion"] == pl.Float64
    assert frame.schema["epoch"] == pl.Datetime("us")
    assert frame.schema["epoch_date"] == pl.Date


def test_epoch_date_is_derived_from_the_epoch_timestamp() -> None:
    frame = gp.to_frame(_gp_rows())

    derived = frame.select((pl.col("epoch").dt.date() == pl.col("epoch_date")).all()).item()
    assert derived is True


def test_rows_are_sorted_deterministically() -> None:
    """Space-Track does not promise a stable order between identical queries,
    and an unstable order would defeat byte-identical re-runs."""
    rows = _gp_rows()
    forward = gp.to_frame(rows)
    reversed_ = gp.to_frame(list(reversed(rows)))

    assert forward.equals(reversed_)


def test_column_order_is_fixed() -> None:
    assert gp.to_frame(_gp_rows()).columns == list(gp.BRONZE_COLUMNS)


def test_an_empty_response_yields_an_empty_typed_frame() -> None:
    frame = gp.to_frame([])

    assert frame.is_empty()
    assert frame.columns == list(gp.BRONZE_COLUMNS)


def test_an_empty_response_has_the_same_types_as_a_full_one() -> None:
    """An empty array is how a throttled Space-Track answers. The frame built
    from it must still match the bronze table's types; SATCAT once returned
    every column as a string here."""
    ingest_date = dt.date(2024, 5, 20)

    assert gp.to_frame([]).schema == gp.to_frame(_gp_rows()).schema
    assert (
        satcat.to_frame([], ingest_date).schema
        == satcat.to_frame(_satcat_rows(), ingest_date).schema
    )


def test_null_decay_date_survives_casting() -> None:
    frame = gp.to_frame(_gp_rows())

    assert frame["decay_date"].is_null().all()


# -- validation and quarantine ---------------------------------------------


def test_the_impossible_record_is_quarantined_and_the_rest_survive() -> None:
    """The fixture's last record has mean motion 148 rev/day and eccentricity
    1.8 -- neither is a physically possible orbit."""
    frame = gp.to_frame(_gp_rows())

    outcome = validate(frame, gp.schema, source=gp.SOURCE)

    assert outcome.quarantined_count == 1
    assert outcome.valid_count == frame.height - 1
    assert not outcome.is_clean


def test_a_quarantined_row_keeps_its_whole_payload_and_a_reason() -> None:
    frame = gp.to_frame(_gp_rows())

    outcome = validate(frame, gp.schema, source=gp.SOURCE)
    record = outcome.quarantined.row(0, named=True)

    assert record["source"] == gp.SOURCE
    assert "mean_motion" in record["failure_reason"] or "eccentricity" in (record["failure_reason"])
    payload = json.loads(record["payload"])
    assert payload["mean_motion"] == 148.0
    assert "norad_id" in payload


def test_one_bad_row_does_not_lose_the_batch() -> None:
    """The whole point of quarantining rather than raising."""
    frame = gp.to_frame(_gp_rows())

    outcome = validate(frame, gp.schema, source=gp.SOURCE)

    assert outcome.valid_count > 0
    assert outcome.valid_count + outcome.quarantined_count == frame.height


def test_clean_data_produces_an_empty_quarantine() -> None:
    frame = gp.to_frame(_gp_rows()[:-1])

    outcome = validate(frame, gp.schema, source=gp.SOURCE)

    assert outcome.is_clean
    assert outcome.quarantined_count == 0


def test_validating_an_empty_frame_is_not_an_error() -> None:
    outcome = validate(gp.to_frame([]), gp.schema, source=gp.SOURCE)

    assert outcome.valid_count == 0
    assert outcome.is_clean


# -- satcat ----------------------------------------------------------------


def test_satcat_casts_and_is_partitioned_by_ingest_date() -> None:
    day = dt.date(2026, 9, 18)

    frame = satcat.to_frame(_satcat_rows(), day)
    outcome = validate(frame, satcat.schema, source=satcat.SOURCE)

    assert outcome.is_clean
    assert frame.schema["norad_id"] == pl.Int64
    assert frame.schema["launch_date"] == pl.Date
    assert (frame["ingest_date"] == day).all()


def test_satcat_rows_are_sorted_by_norad_id() -> None:
    frame = satcat.to_frame(_satcat_rows(), dt.date(2026, 9, 18))

    assert frame["norad_id"].to_list() == sorted(frame["norad_id"].to_list())


# -- omni ------------------------------------------------------------------


def test_omni_keeps_the_servers_units_untouched() -> None:
    """Kp arrives multiplied by ten; converting it belongs downstream."""
    rows = [
        {
            "Time": "2024-05-11T00:30:00.000Z",
            "F10_INDEX1800": 218.0,
            "KP1800": 90.0,
            "DST1800": -406.0,
            "AP_INDEX1800": 400.0,
        }
    ]

    frame = omni.to_frame(rows)
    outcome = validate(frame, omni.schema, source=omni.SOURCE)

    assert outcome.is_clean
    assert frame["kp_x10"][0] == 90
    assert frame["dst_nt"][0] == -406
    assert frame["epoch_date"][0] == dt.date(2024, 5, 11)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("F10_INDEX1800", 9999.0),  # far beyond any recorded solar flux
        ("KP1800", 130.0),  # Kp cannot exceed 9.0, i.e. 90
        ("DST1800", -5000.0),  # no storm has come close
        ("AP_INDEX1800", 900.0),  # Ap is bounded at 400 by construction
    ],
)
def test_impossible_space_weather_is_quarantined(column: str, value: float) -> None:
    row = {
        "Time": "2024-05-11T00:30:00.000Z",
        "F10_INDEX1800": 218.0,
        "KP1800": 77.0,
        "DST1800": -157.0,
        "AP_INDEX1800": 179.0,
    }
    row[column] = value

    outcome = validate(omni.to_frame([row]), omni.schema, source=omni.SOURCE)

    assert outcome.quarantined_count == 1
    assert outcome.valid_count == 0
