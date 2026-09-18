"""Bronze landing: partition scoping, idempotency and byte-identity.

These run a real dlt pipeline against a real Iceberg table in a temporary
directory. No network is involved -- the frames are built in the test.

The byte-identity check here is the automated form of Phase 1's acceptance
criterion: re-running a partition must produce identical Parquet bytes and must
not disturb any other partition.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import polars as pl
import pytest

from starlink_drag.config import Settings
from starlink_drag.ingest.bronze import (
    AUDIT_TABLE,
    OMNI_SPEC,
    QUARANTINE_TABLE,
    lake_root,
    land,
    partition_digest,
    read_table,
    write,
)
from starlink_drag.schemas import omni

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """A settings object pointing at a lake inside this test's temp directory."""
    return Settings(data_dir=tmp_path / "d")


def _frame(day: str, hours: int = 3, dst: int = -50) -> pl.DataFrame:
    rows = [
        {
            "Time": f"{day}T{hour:02d}:30:00.000Z",
            "F10_INDEX1800": 218.0 + hour,
            "KP1800": 77.0,
            "DST1800": float(dst),
            "AP_INDEX1800": 179.0,
        }
        for hour in range(hours)
    ]
    return omni.to_frame(rows)


def _digest(settings: Settings, day: str) -> list[str]:
    return partition_digest(settings, OMNI_SPEC.table, OMNI_SPEC.partition_column, day)


# -- the acceptance criterion ----------------------------------------------


def test_rerunning_a_partition_produces_byte_identical_output(
    settings: Settings,
) -> None:
    write(_frame("2024-05-10"), OMNI_SPEC, settings)
    first = _digest(settings, "2024-05-10")

    write(_frame("2024-05-10"), OMNI_SPEC, settings)
    second = _digest(settings, "2024-05-10")

    assert first, "expected at least one data file"
    assert first == second


def test_rerunning_a_partition_leaves_other_partitions_untouched(
    settings: Settings,
) -> None:
    write(_frame("2024-05-10"), OMNI_SPEC, settings)
    write(_frame("2024-05-11"), OMNI_SPEC, settings)
    untouched = _digest(settings, "2024-05-11")

    write(_frame("2024-05-10"), OMNI_SPEC, settings)

    assert _digest(settings, "2024-05-11") == untouched


def test_reloading_a_partition_replaces_rather_than_duplicates(
    settings: Settings,
) -> None:
    write(_frame("2024-05-10", hours=3), OMNI_SPEC, settings)
    write(_frame("2024-05-10", hours=3), OMNI_SPEC, settings)

    rows = read_table(settings, OMNI_SPEC.table, row_filter="epoch_date = '2024-05-10'")
    assert rows.height == 3


def test_corrected_data_supersedes_the_earlier_load(settings: Settings) -> None:
    """A partition re-run with different values must reflect the new values."""
    write(_frame("2024-05-10", dst=-50), OMNI_SPEC, settings)
    write(_frame("2024-05-10", dst=-406), OMNI_SPEC, settings)

    rows = read_table(settings, OMNI_SPEC.table, row_filter="epoch_date = '2024-05-10'")
    assert set(rows["dst_nt"].to_list()) == {-406}


# -- partitioning ----------------------------------------------------------


def test_each_day_lands_in_its_own_partition_directory(settings: Settings) -> None:
    frame = pl.concat([_frame("2024-05-10"), _frame("2024-05-11")])

    outcome = write(frame, OMNI_SPEC, settings)

    root = Path(lake_root(settings)) / "bronze" / OMNI_SPEC.table / "data"
    partitions = sorted(p.name for p in root.iterdir() if p.is_dir())
    assert partitions == ["epoch_date=2024-05-10", "epoch_date=2024-05-11"]
    assert outcome.partitions == ("2024-05-10", "2024-05-11")


def test_an_empty_frame_writes_nothing(settings: Settings) -> None:
    outcome = write(omni.to_frame([]), OMNI_SPEC, settings)

    assert outcome.rows_written == 0
    assert outcome.partitions == ()


# -- quarantine and audit --------------------------------------------------


def test_bad_rows_are_quarantined_while_good_rows_land(settings: Settings) -> None:
    good = _frame("2024-05-10", hours=2)
    bad = omni.to_frame(
        [
            {
                "Time": "2024-05-10T05:30:00.000Z",
                "F10_INDEX1800": 9999.0,  # impossible
                "KP1800": 77.0,
                "DST1800": -157.0,
                "AP_INDEX1800": 179.0,
            }
        ]
    )

    outcome = land(
        pl.concat([good, bad]),
        OMNI_SPEC,
        settings,
        schema=omni.schema,
        source=omni.SOURCE,
    )

    assert outcome.rows_written == 2
    assert outcome.rows_quarantined == 1

    quarantine = read_table(settings, QUARANTINE_TABLE)
    assert quarantine.height == 1
    assert json.loads(quarantine["payload"][0])["f10_7_sfu"] == 9999.0


def test_the_audit_table_records_when_a_load_happened(settings: Settings) -> None:
    """ingest_timestamp lives here, not in the data files -- that is what keeps
    the data files byte-identical across runs."""
    before = dt.datetime.now(dt.UTC)

    land(
        _frame("2024-05-10"),
        OMNI_SPEC,
        settings,
        schema=omni.schema,
        source=omni.SOURCE,
    )

    audit = read_table(settings, AUDIT_TABLE)
    assert audit.height == 1
    record = audit.row(0, named=True)
    assert record["table_name"] == OMNI_SPEC.table
    assert record["rows_written"] == 3
    stamped = record["ingest_timestamp"]
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=dt.UTC)
    assert stamped >= before.replace(microsecond=0)


def test_no_wall_clock_column_reaches_the_data_files(settings: Settings) -> None:
    """If an ingest timestamp were a bronze column, byte-identity would be
    impossible by construction."""
    write(_frame("2024-05-10"), OMNI_SPEC, settings)

    rows = read_table(settings, OMNI_SPEC.table, row_filter="epoch_date = '2024-05-10'")
    assert rows.columns == list(omni.BRONZE_COLUMNS)
    assert not any("ingest" in c or c.startswith("_dlt") for c in rows.columns)
