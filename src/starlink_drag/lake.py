"""Where the lake lives, and how each tool that touches it gets in.

Bronze is reached by three libraries: dlt writes it, pyiceberg reads its
metadata, DuckDB reads its Parquet. Each wants the same address and keys in a
different shape, so every shape is built here, from one set of settings, and
the three cannot disagree.

``local`` is a directory under ``data/``. ``r2`` is any S3-compatible bucket:
Cloudflare R2 in production, or SeaweedFS from ``infra/docker`` to rehearse it.

Until 2026-09-26 only ``local`` worked. The endpoint and keys existed in the
settings and in docker-compose and were never handed to anything, so the
Docker stack started but could not store a row. Phase 4 had checked that the
containers start, not that data flows through them. See ADR-0010.
"""

from __future__ import annotations

from typing import Any, Final
from urllib.parse import urlparse

import dlt
import fsspec
from dlt.common.configuration.specs import AwsCredentials

from starlink_drag.config import Settings


def is_remote(settings: Settings) -> bool:
    return settings.lake.backend != "local"


def root(settings: Settings) -> str:
    """The lake's root: a local directory, or ``s3://<bucket>``."""
    if not is_remote(settings):
        return str((settings.data_dir / "lake").resolve())
    return f"s3://{settings.lake.bucket}"


def dlt_destination(settings: Settings) -> Any:
    """dlt's filesystem destination, with keys and endpoint when remote.

    dlt hands the same credentials on to pyiceberg when it writes an Iceberg
    table, so this one object configures the whole write path.
    """
    if not is_remote(settings):
        return dlt.destinations.filesystem(root(settings))
    lake = settings.lake
    credentials = AwsCredentials(
        aws_access_key_id=lake.access_key_id.get_secret_value(),
        aws_secret_access_key=lake.secret_access_key.get_secret_value(),
        endpoint_url=lake.endpoint_url,
        region_name=lake.region,
    )
    return dlt.destinations.filesystem(bucket_url=root(settings), credentials=credentials)


def iceberg_properties(settings: Settings) -> dict[str, str]:
    """pyiceberg FileIO properties for reading tables. Empty for a local lake."""
    if not is_remote(settings):
        return {}
    lake = settings.lake
    return {
        "s3.endpoint": lake.endpoint_url,
        "s3.access-key-id": lake.access_key_id.get_secret_value(),
        "s3.secret-access-key": lake.secret_access_key.get_secret_value(),
        "s3.region": lake.region,
    }


def filesystem(settings: Settings) -> fsspec.AbstractFileSystem:
    """An fsspec filesystem over the lake, for listing and reading raw files.

    Uncached, deliberately. s3fs caches directory listings inside a process,
    and the table's current version is found by listing its metadata folder:
    with the cache on, a write followed by a read in the same process saw the
    table as it was before the write. Found by the lake copy's row-count check,
    which reported 54 of 180 rows copied when all 180 had been.
    """
    if not is_remote(settings):
        return fsspec.filesystem("file")
    lake = settings.lake
    return fsspec.filesystem(
        "s3",
        key=lake.access_key_id.get_secret_value(),
        secret=lake.secret_access_key.get_secret_value(),
        endpoint_url=lake.endpoint_url,
        client_kwargs={"region_name": lake.region},
        use_listings_cache=False,
        skip_instance_cache=True,
    )


#: Reuse HTTP connections across files. Off by default in DuckDB 1.5, and then
#: every Parquet file read opens and closes its own: one scan of the element
#: sets (about 2,500 files) left ~4,900 sockets in TIME_WAIT for a minute, and
#: dbt's back-to-back tests ran the container out of its ~28,000 local ports --
#: "Could not connect to server" from the sixth test on. With it: none left.
DUCKDB_REMOTE_SETTINGS: Final = ("SET httpfs_connection_caching = true",)


def duckdb_setup(settings: Settings) -> list[str]:
    """Statements a DuckDB connection runs before it reads a remote lake.

    The lake's secret, then :data:`DUCKDB_REMOTE_SETTINGS`. Path-style URLs,
    because the local S3 server serves buckets at a path rather than as a
    subdomain, and R2 accepts either. Nothing for a local lake.
    """
    if not is_remote(settings):
        return []
    lake = settings.lake
    endpoint = urlparse(lake.endpoint_url)
    options = {
        "KEY_ID": lake.access_key_id.get_secret_value(),
        "SECRET": lake.secret_access_key.get_secret_value(),
        "ENDPOINT": endpoint.netloc,
        "REGION": lake.region,
        "URL_STYLE": "path",
    }
    rendered = ", ".join(f"{key} '{_quote(value)}'" for key, value in options.items())
    use_ssl = "true" if endpoint.scheme == "https" else "false"
    # The secret first: creating an s3 secret loads httpfs, which owns the setting.
    secret = f"CREATE OR REPLACE SECRET lake (TYPE s3, {rendered}, USE_SSL {use_ssl})"
    return [secret, *DUCKDB_REMOTE_SETTINGS]


def dbt_environment(settings: Settings) -> dict[str, str]:
    """Variables the dbt profile reads to build the same secret for dbt.

    dbt reads real environment variables, not ``.env``, so whoever starts dbt
    passes these through explicitly.
    """
    lake = settings.lake
    endpoint = urlparse(lake.endpoint_url)
    return {
        "LAKE_BACKEND": lake.backend,
        "LAKE_S3_ENDPOINT": endpoint.netloc,
        "LAKE_S3_USE_SSL": "true" if endpoint.scheme == "https" else "false",
        "LAKE_ACCESS_KEY_ID": lake.access_key_id.get_secret_value(),
        "LAKE_SECRET_ACCESS_KEY": lake.secret_access_key.get_secret_value(),
        "LAKE_REGION": lake.region,
    }


def _quote(value: str) -> str:
    return value.replace("'", "''")
