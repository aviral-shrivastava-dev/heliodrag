"""The lake on S3-compatible storage: MinIO standing in for Cloudflare R2.

Every other test runs against a lake on local disk. These run the same code
against a real S3 API, because until 2026-09-26 that path did not work at all:
the endpoint and keys were in the settings and in docker-compose and were never
handed to dlt, pyiceberg or DuckDB. The Docker stack started; nothing could be
stored in it. Checking that containers start is not checking that data flows.

Skipped unless ``LAKE_TEST_S3_ENDPOINT`` names a MinIO. CI starts one; locally::

    docker compose -f infra/docker/docker-compose.yml up -d minio
    LAKE_TEST_S3_ENDPOINT=http://localhost:9000 uv run pytest tests/integration/test_s3_lake.py

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

ENDPOINT = os.environ.get("LAKE_TEST_S3_ENDPOINT", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not ENDPOINT, reason="set LAKE_TEST_S3_ENDPOINT to run against MinIO"),
]


@pytest.fixture
def s3_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    bucket = f"test-{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("LAKE_BACKEND", "r2")
    monkeypatch.setenv("LAKE_ENDPOINT_URL", ENDPOINT)
    monkeypatch.setenv("LAKE_BUCKET", bucket)
    monkeypatch.setenv("LAKE_ACCESS_KEY_ID", os.environ.get("LAKE_TEST_S3_KEY", "minioadmin"))
    monkeypatch.setenv(
        "LAKE_SECRET_ACCESS_KEY", os.environ.get("LAKE_TEST_S3_SECRET", "minioadmin")
    )
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
    """Land in MinIO, point DuckDB at it, run the real dbt build, read the marts."""
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
