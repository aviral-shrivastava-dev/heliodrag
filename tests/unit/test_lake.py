"""How each tool reaches the lake: one set of settings, four shapes.

No storage is touched here -- the shapes are checked directly. That the shapes
actually work against an S3 API is tests/integration/test_s3_lake.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from starlink_drag import lake
from starlink_drag.config import Settings


def _remote(monkeypatch: pytest.MonkeyPatch, endpoint: str = "http://seaweedfs:8333") -> Settings:
    monkeypatch.setenv("LAKE_BACKEND", "r2")
    monkeypatch.setenv("LAKE_ENDPOINT_URL", endpoint)
    monkeypatch.setenv("LAKE_BUCKET", "atlas")
    monkeypatch.setenv("LAKE_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("LAKE_SECRET_ACCESS_KEY", "it's-secret")  # pragma: allowlist secret
    monkeypatch.setenv("LAKE_REGION", "auto")
    return Settings()


def test_a_local_lake_is_a_directory_and_needs_no_keys() -> None:
    settings = Settings()

    assert not lake.is_remote(settings)
    assert lake.root(settings).endswith("lake")
    assert lake.iceberg_properties(settings) == {}
    assert lake.duckdb_setup(settings) == []


def test_a_remote_lake_is_a_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _remote(monkeypatch)

    assert lake.is_remote(settings)
    assert lake.root(settings) == "s3://atlas"


def test_pyiceberg_gets_the_endpoint_and_the_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    properties = lake.iceberg_properties(_remote(monkeypatch))

    assert properties["s3.endpoint"] == "http://seaweedfs:8333"
    assert properties["s3.access-key-id"] == "key"
    assert properties["s3.secret-access-key"] == "it's-secret"  # pragma: allowlist secret


def test_duckdb_gets_a_path_style_secret_without_ssl_for_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret, *_ = lake.duckdb_setup(_remote(monkeypatch))

    assert "ENDPOINT 'seaweedfs:8333'" in secret, "DuckDB wants host:port, no scheme"
    assert "URL_STYLE 'path'" in secret, "the local S3 server serves buckets at a path"
    assert "USE_SSL false" in secret
    assert "SECRET 'it''s-secret'" in secret, "quotes in a key are escaped, not injected"


def test_an_https_endpoint_turns_ssl_on(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _remote(monkeypatch, endpoint="https://account.r2.cloudflarestorage.com")

    secret, *_ = lake.duckdb_setup(settings)
    environment = lake.dbt_environment(settings)

    assert "USE_SSL true" in secret
    assert environment["LAKE_S3_USE_SSL"] == "true"
    assert environment["LAKE_S3_ENDPOINT"] == "account.r2.cloudflarestorage.com"


def test_every_remote_duckdb_connection_reuses_http_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without connection reuse, dbt's tests on the full history ran the
    container out of local ports. The Python connections and dbt's profile must
    both turn it on."""
    setup = lake.duckdb_setup(_remote(monkeypatch))
    profile = Path(__file__).parents[2] / "transform" / "profiles.yml"
    dbt_settings = yaml.safe_load(profile.read_text(encoding="utf-8"))["starlink_drag"]["outputs"][
        "dev"
    ]["settings"]

    assert "SET httpfs_connection_caching = true" in setup
    assert setup[0].startswith("CREATE OR REPLACE SECRET"), "the secret loads httpfs first"
    assert dbt_settings["httpfs_connection_caching"] is True


def test_dbt_gets_the_same_address_and_keys_as_duckdb(monkeypatch: pytest.MonkeyPatch) -> None:
    """The profile builds dbt's secret from these, so they must describe the
    same lake as the secret the warehouse sync creates."""
    environment = lake.dbt_environment(_remote(monkeypatch))

    assert environment == {
        "LAKE_BACKEND": "r2",
        "LAKE_S3_ENDPOINT": "seaweedfs:8333",
        "LAKE_S3_USE_SSL": "false",
        "LAKE_ACCESS_KEY_ID": "key",
        "LAKE_SECRET_ACCESS_KEY": "it's-secret",  # pragma: allowlist secret
        "LAKE_REGION": "auto",
    }


def test_dlt_writes_to_the_bucket_with_the_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    destination = lake.dlt_destination(_remote(monkeypatch))
    config = destination.configuration(None, accept_partial=True)

    assert config.bucket_url == "s3://atlas"
    assert config.credentials.endpoint_url == "http://seaweedfs:8333"
    assert config.credentials.aws_access_key_id == "key"
