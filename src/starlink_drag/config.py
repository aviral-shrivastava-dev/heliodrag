"""Runtime configuration.

Every knob in this project is an environment variable read here. Nothing else
in the codebase calls ``os.environ``. Secrets are wrapped in ``SecretStr`` so
that an accidental ``repr`` or a Dagster run-config dump cannot leak them.

See ``.env.example`` for the full list and how to obtain each credential.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = ".env"

LakeBackend = Literal["local", "r2"]


class SpaceTrackSettings(BaseSettings):
    """Credentials and limits for https://www.space-track.org.

    The rate limits are not advisory. Space-Track blocks accounts that exceed
    them, so they are configuration rather than constants only to let tests
    drive the limiter quickly -- never to raise them in production.
    """

    model_config = SettingsConfigDict(env_prefix="SPACETRACK_", env_file=_ENV_FILE, extra="ignore")

    identity: str = ""
    password: SecretStr = SecretStr("")
    base_url: str = "https://www.space-track.org"

    requests_per_minute: int = Field(default=29, gt=0, le=29)
    requests_per_hour: int = Field(default=299, gt=0, le=299)
    max_retries: int = Field(default=5, ge=0)
    norad_ids_per_request: int = Field(default=200, gt=0)

    @property
    def is_configured(self) -> bool:
        return bool(self.identity) and bool(self.password.get_secret_value())


class HapiSettings(BaseSettings):
    """NASA OMNI via the SPDF HAPI server. Public, no authentication."""

    model_config = SettingsConfigDict(env_prefix="HAPI_", env_file=_ENV_FILE, extra="ignore")

    base_url: str = "https://cdaweb.gsfc.nasa.gov/hapi"
    dataset: str = "OMNI2_H0_MRG1HR"
    timeout_seconds: float = Field(default=60.0, gt=0)


class LakeSettings(BaseSettings):
    """Object storage holding the bronze Iceberg tables.

    ``local`` points at MinIO from ``infra/docker``; ``r2`` at Cloudflare R2.
    The two are S3-compatible, so only the endpoint and credentials differ.
    """

    model_config = SettingsConfigDict(env_prefix="LAKE_", env_file=_ENV_FILE, extra="ignore")

    backend: LakeBackend = "local"
    endpoint_url: str = "http://localhost:9000"
    bucket: str = "starlink-drag-atlas"
    access_key_id: SecretStr = SecretStr("")
    secret_access_key: SecretStr = SecretStr("")
    region: str = "auto"


class Settings(BaseSettings):
    """Top-level settings object. Obtain it via :func:`get_settings`."""

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    data_dir: Path = Path("data")
    duckdb_path: Path = Path("data/atlas.duckdb")

    pipeline_start_date: dt.date = dt.date(2020, 1, 1)
    log_level: str = "INFO"

    spacetrack: SpaceTrackSettings = Field(default_factory=SpaceTrackSettings)
    hapi: HapiSettings = Field(default_factory=HapiSettings)
    lake: LakeSettings = Field(default_factory=LakeSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read from the environment once.

    Cached so that Dagster ops, the CLI and dbt's Python hooks all observe the
    same values. Call ``get_settings.cache_clear()`` in tests that monkeypatch
    the environment.
    """
    return Settings()
