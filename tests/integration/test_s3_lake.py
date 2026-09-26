"""The lake on S3-compatible storage: SeaweedFS standing in for Cloudflare R2.

Every other test runs against a lake on local disk. These run the same code
against a real S3 API, because until 2026-09-26 that path did not work at all:
the endpoint and keys were in the settings and in docker-compose and were never
handed to dlt, pyiceberg or DuckDB. The Docker stack started; nothing could be
stored in it. Checking that containers start is not checking that data flows.

Skipped unless ``S3_TEST_ENDPOINT`` names an S3 server. CI starts SeaweedFS;
locally::

    docker compose -f infra/docker/docker-compose.yml up -d seaweedfs
    S3_TEST_ENDPOINT=http://localhost:8333 uv run pytest tests/integration/test_s3_lake.py

Each test gets its own bucket, deleted afterwards.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import duckdb
import polars as pl
import pytest

from starlink_drag import lake, warehouse
from starlink_drag.config import Settings
from starlink_drag.ingest import bronze
from starlink_drag.ingest.satcat import norad_ids
from starlink_drag.schemas import omni
from tests.integration.fixture_warehouse import (
    add_empty_bookkeeping_tables,
    bronze_frames,
    run_dbt,
    summary,
    table,
)

# Deliberately not LAKE_*: the suite's isolation strips every LAKE_ variable
# before each test, which once made the keys silently fall back to defaults.
ENDPOINT = os.environ.get("S3_TEST_ENDPOINT", "")
ACCESS_KEY = os.environ.get("S3_TEST_ACCESS_KEY", "atlas")
SECRET_KEY = os.environ.get("S3_TEST_SECRET_KEY", "atlas-local-only")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not ENDPOINT, reason="set S3_TEST_ENDPOINT to run against an S3 server"),
]


@pytest.fixture
def s3_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    bucket = f"test-{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("LAKE_BACKEND", "r2")
    monkeypatch.setenv("LAKE_ENDPOINT_URL", ENDPOINT)
    monkeypatch.setenv("LAKE_BUCKET", bucket)
    monkeypatch.setenv("LAKE_ACCESS_KEY_ID", ACCESS_KEY)
    monkeypatch.setenv("LAKE_SECRET_ACCESS_KEY", SECRET_KEY)
    monkeypatch.setenv("LAKE_REGION", "us-east-1")
    settings = Settings(data_dir=tmp_path / "d")

    storage = lake.filesystem(settings)
    storage.mkdir(bucket)
    yield settings
    storage.rm(bucket, recursive=True)


def _omni(day: str) -> pl.DataFrame:
    return omni.to_frame(
        [
            {
                "Time": f"{day}T{hour:02d}:30:00.000Z",
                "F10_INDEX1800": 200.0,
                "KP1800": 30.0,
                "DST1800": -20.0,
                "AP_INDEX1800": 9.0,
            }
            for hour in range(3)
        ]
    )


def test_bronze_round_trips_through_s3(s3_settings: Settings) -> None:
    frame = _omni("2024-05-10")

    bronze.write(frame, bronze.OMNI_SPEC, s3_settings)
    back = bronze.read_table(s3_settings, bronze.OMNI_TABLE)

    assert lake.root(s3_settings).startswith("s3://")
    assert back.height == 3
    assert [str(day) for day in back["epoch_date"].unique().to_list()] == ["2024-05-10"]


def test_a_read_sees_the_write_just_before_it(s3_settings: Settings) -> None:
    """s3fs caches directory listings in-process, and a table's current version
    is found by listing its metadata. With the cache on, the second write below
    was invisible to the read after it -- found when the lake copy reported 54
    of 180 rows copied."""
    frame = _omni("2024-05-10")
    bronze.write(frame, bronze.OMNI_SPEC, s3_settings)
    assert bronze.read_table(s3_settings, bronze.OMNI_TABLE).height == 3

    bronze.write(_omni("2024-05-11"), bronze.OMNI_SPEC, s3_settings)

    assert bronze.read_table(s3_settings, bronze.OMNI_TABLE).height == 6


def test_identical_input_is_byte_identical_on_s3(s3_settings: Settings) -> None:
    """The byte-identity guarantee (ADR-0004) must hold on object storage too."""
    frame = _omni("2024-05-10")
    bronze.write(frame, bronze.OMNI_SPEC, s3_settings)
    bronze.write(frame, bronze.OMNI_SPEC, s3_settings)

    digests = bronze.partition_digest(s3_settings, bronze.OMNI_TABLE, "epoch_date", "2024-05-10")

    assert len(digests) == 2
    assert digests[0] == digests[1]


def test_the_catalogue_is_read_from_s3(s3_settings: Settings) -> None:
    catalogue = bronze_frames()["satcat"]
    bronze.write(catalogue, bronze.SATCAT_SPEC, s3_settings)

    ids = norad_ids(s3_settings)

    assert ids == sorted(catalogue["norad_id"].unique().to_list())


def test_the_whole_pipeline_builds_from_an_s3_lake(s3_settings: Settings, tmp_path: Path) -> None:
    """Land in S3, point DuckDB at it, run the real dbt build, read the marts."""
    frames = bronze_frames()
    for name, spec in (
        ("gp_history", bronze.GP_SPEC),
        ("satcat", bronze.SATCAT_SPEC),
        ("omni", bronze.OMNI_SPEC),
    ):
        bronze.write(frames[name], spec, s3_settings)

    database = tmp_path / "atlas.duckdb"
    synced = {result.view: result for result in warehouse.sync(s3_settings, database=database)}
    assert all(synced[view].created for view in ("gp_history", "satcat", "omni"))
    with duckdb.connect(str(database)) as con:
        add_empty_bookkeeping_tables(con)

    result = run_dbt(database, tmp_path, env=lake.dbt_environment(s3_settings))

    assert result.returncode == 0, result.stdout[-4000:]
    assert summary(result.stdout)["ERROR"] == 0
    assert table(database, "fct_daily_decay").height > 0


def test_a_local_lake_copies_into_s3_exactly(s3_settings: Settings, tmp_path: Path) -> None:
    """The route that moves the real 2020-to-present lake into the Docker stack
    without a single API call. Every table's count must survive the copy."""
    from starlink_drag.lake_copy import copy_lake, total_rows

    source = s3_settings.model_copy(
        update={
            "data_dir": tmp_path / "source",
            "lake": s3_settings.lake.model_copy(update={"backend": "local"}),
        }
    )
    frames = bronze_frames()
    for name, spec in (
        ("gp_history", bronze.GP_SPEC),
        ("satcat", bronze.SATCAT_SPEC),
        ("omni", bronze.OMNI_SPEC),
    ):
        bronze.write(frames[name], spec, source)

    target = s3_settings.model_copy(update={"data_dir": tmp_path / "copy"})
    results = copy_lake(source, target, report=lambda line: None)

    copied = {result.table: result for result in results}
    assert all(result.verified for result in results)
    assert copied[bronze.GP_TABLE].target_rows == frames["gp_history"].height
    assert total_rows(target, bronze.OMNI_TABLE) == frames["omni"].height

    # A copy that died is simply run again: what is complete is skipped, and
    # nothing is duplicated.
    written: list[str] = []
    again = copy_lake(source, target, report=written.append)
    assert all(result.verified for result in again)
    assert not [line for line in written if "rows" in line and "->" not in line]
    assert total_rows(target, bronze.GP_TABLE) == frames["gp_history"].height
