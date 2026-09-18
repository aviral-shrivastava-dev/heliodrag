"""Test-wide isolation.

Two guarantees every test in this suite relies on:

1. No test reads the developer's real ``.env``. Settings objects resolve their
   env file relative to the working directory, so we run each test from an
   empty temporary directory.
2. No test reaches the network. CI has no Space-Track credentials and must
   never acquire any; client tests run against fixtures in ``tests/fixtures``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from starlink_drag.config import get_settings

_MANAGED_PREFIXES = ("SPACETRACK_", "HAPI_", "LAKE_")
_MANAGED_NAMES = ("DATA_DIR", "DUCKDB_PATH", "PIPELINE_START_DATE", "LOG_LEVEL")

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Run each test in an empty CWD with no project environment variables."""
    for name in list(os.environ):
        if name.startswith(_MANAGED_PREFIXES) or name in _MANAGED_NAMES:
            monkeypatch.delenv(name, raising=False)

    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory of committed, real API responses used instead of the network."""
    return FIXTURES_DIR
