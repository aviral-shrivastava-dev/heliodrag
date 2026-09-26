"""Warehouses for integration tests, built from synthetic bronze."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.fixture_warehouse import build_bronze, run_dbt


@pytest.fixture
def seeded_warehouse(tmp_path: Path) -> Path:
    """Bronze tables shaped exactly like the real ones, and nothing built yet.

    Built through the production schema modules, so a change to the bronze
    column set breaks these tests rather than silently diverging from them.
    """
    database = tmp_path / "fixture.duckdb"
    build_bronze(database)
    return database


@pytest.fixture(scope="module")
def built_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same bronze after a real ``dbt build``: every gold mart populated.

    Module-scoped because the build takes seconds and the tests using it only
    read. Named like the production file, as the explorer's queries expect.
    """
    work_dir = tmp_path_factory.mktemp("built")
    database = work_dir / "atlas.duckdb"
    build_bronze(database)
    result = run_dbt(database, work_dir)
    assert result.returncode == 0, result.stdout[-4000:]
    return database
