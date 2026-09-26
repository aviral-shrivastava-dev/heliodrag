"""The lake copy's arithmetic and refusals. The copy itself runs against a real
S3 server in tests/integration/test_s3_lake.py."""

from __future__ import annotations

import datetime as dt

import pytest
from typer.testing import CliRunner

from starlink_drag.cli import app
from starlink_drag.config import Settings
from starlink_drag.lake_copy import TableCopy, chunks, copy_lake


def test_chunks_cover_a_range_without_gaps_or_overlap() -> None:
    spans = list(chunks(dt.date(2025, 12, 25), dt.date(2026, 1, 12), days=10))

    assert spans == [
        (dt.date(2025, 12, 25), dt.date(2026, 1, 4)),
        (dt.date(2026, 1, 4), dt.date(2026, 1, 12)),
    ]


def test_a_chunk_must_be_at_least_a_day() -> None:
    with pytest.raises(ValueError, match="at least one day"):
        list(chunks(dt.date(2026, 1, 1), dt.date(2026, 1, 2), days=0))


def test_it_only_copies_local_to_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    local = Settings()
    monkeypatch.setenv("LAKE_BACKEND", "r2")
    remote = Settings()

    with pytest.raises(ValueError, match="local lake into a remote one"):
        copy_lake(local, local)
    with pytest.raises(ValueError, match="local lake into a remote one"):
        copy_lake(remote, local)


def test_a_count_mismatch_is_reported_as_one() -> None:
    assert TableCopy("t", 10, 10).verified
    assert not TableCopy("t", 10, 9).verified
    assert "MISMATCH" in TableCopy("t", 10, 9).describe()


def test_the_command_refuses_when_there_is_nowhere_to_copy_to() -> None:
    result = CliRunner().invoke(app, ["lake-copy"])

    assert result.exit_code == 1
    assert "LAKE_BACKEND=r2" in result.output
