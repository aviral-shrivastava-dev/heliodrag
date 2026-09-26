"""Fetch strategy for Space-Track elements. No network, no lake.

The batching shape here is a performance contract, not a style choice. Writing
costs roughly 1.5 seconds per *partition touched*, nearly independent of row
count, so landing each NORAD batch separately rewrites every day in the window
once per batch. Over a year that is ~12,800 partition writes instead of 366.
The tests below pin the shape so it cannot be undone by accident.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
from collections.abc import Sequence
from typing import Any

import polars as pl
import pytest

from starlink_drag.config import Settings
from starlink_drag.ingest import gp as ingest_gp_module
from starlink_drag.ingest.gp import GpIngestReport, ingest_gp, windows
from starlink_drag.schemas import gp as gp_schema


class FakeSpaceTrack:
    """Returns one element record per satellite per day in the window."""

    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.calls: list[tuple[int, dt.date, dt.date]] = []
        self.fail_on = fail_on or set()
        self.closed = False

    def gp_history(
        self, norad_ids: Sequence[int], start: dt.date, end: dt.date
    ) -> list[dict[str, Any]]:
        self.calls.append((len(norad_ids), start, end))
        if len(self.calls) in self.fail_on:
            raise RuntimeError("space-track said no")
        rows = []
        for sat in norad_ids:
            day = start
            while day < end:
                rows.append(_record(sat, day))
                day += dt.timedelta(days=1)
        return rows

    def close(self) -> None:
        self.closed = True


def _record(norad_id: int, day: dt.date) -> dict[str, Any]:
    return {
        "GP_ID": str(norad_id * 10000 + day.toordinal()),
        "NORAD_CAT_ID": str(norad_id),
        "OBJECT_NAME": f"STARLINK-X{norad_id}",
        "OBJECT_ID": "2024-001A",
        "OBJECT_TYPE": "PAYLOAD",
        "EPOCH": f"{day.isoformat()}T03:00:00",
        "MEAN_MOTION": "15.1",
        "ECCENTRICITY": "0.0002",
        "INCLINATION": "53.0",
        "RA_OF_ASC_NODE": "300.0",
        "ARG_OF_PERICENTER": "80.0",
        "MEAN_ANOMALY": "270.0",
        "BSTAR": "0.0002",
        "MEAN_MOTION_DOT": "0.00003",
        "MEAN_MOTION_DDOT": "0.0",
        "SEMIMAJOR_AXIS": "6925.3",
        "PERIOD": "95.5",
        "APOAPSIS": "548.2",
        "PERIAPSIS": "546.1",
        "REV_AT_EPOCH": "100",
        "ELEMENT_SET_NO": "999",
        "EPHEMERIS_TYPE": "0",
        "CLASSIFICATION_TYPE": "U",
        "RCS_SIZE": "LARGE",
        "COUNTRY_CODE": "US",
        "SITE": "AFETR",
        "LAUNCH_DATE": "2024-01-01",
        "DECAY_DATE": None,
        "CREATION_DATE": "2024-01-02T00:00:00",
        "FILE": "1",
        "TLE_LINE0": "0 S",
        "TLE_LINE1": "1 x",
        "TLE_LINE2": "2 x",
    }


@pytest.fixture
def landings(monkeypatch: pytest.MonkeyPatch) -> list[pl.DataFrame]:
    """Capture what would be written, without writing anything."""
    captured: list[pl.DataFrame] = []

    def fake_land(frame: pl.DataFrame, spec: Any, settings: Any, **kwargs: Any) -> Any:
        captured.append(frame)
        from starlink_drag.ingest.bronze import LoadOutcome

        days = tuple(str(d) for d in frame["epoch_date"].unique().sort().to_list())
        return LoadOutcome(spec.table, days, frame.height, 0)

    monkeypatch.setattr(ingest_gp_module, "land", fake_land)
    return captured


def _settings(tmp_path: Any, batch_size: int = 2) -> Settings:
    settings = Settings(data_dir=tmp_path / "d")
    settings.spacetrack.norad_ids_per_request = batch_size
    return settings


# -- windowing -------------------------------------------------------------


def test_windows_split_a_range_into_half_open_spans() -> None:
    got = list(windows(dt.date(2024, 1, 1), dt.date(2024, 1, 10), 4))

    assert got == [
        (dt.date(2024, 1, 1), dt.date(2024, 1, 5)),
        (dt.date(2024, 1, 5), dt.date(2024, 1, 9)),
        (dt.date(2024, 1, 9), dt.date(2024, 1, 10)),
    ]


def test_windows_cover_the_range_without_gaps_or_overlap() -> None:
    spans = list(windows(dt.date(2024, 1, 1), dt.date(2024, 4, 10), 30))

    assert spans[0][0] == dt.date(2024, 1, 1)
    assert spans[-1][1] == dt.date(2024, 4, 10)
    assert all(a[1] == b[0] for a, b in itertools.pairwise(spans))


def test_a_window_must_be_at_least_one_day() -> None:
    with pytest.raises(ValueError):
        list(windows(dt.date(2024, 1, 1), dt.date(2024, 2, 1), 0))


# -- the batching shape (a performance contract) ---------------------------


def test_one_write_per_window_not_one_per_batch(
    tmp_path: Any, landings: list[pl.DataFrame]
) -> None:
    client = FakeSpaceTrack()

    report = ingest_gp(
        _settings(tmp_path, batch_size=2),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 11),
        [1, 2, 3, 4, 5, 6],
        window_days=5,
        client=client,
    )

    # 2 windows x 3 batches of 2 satellites = 6 requests...
    assert report.requests == 6
    assert len(client.calls) == 6
    # ...but only 2 writes, one per window.
    assert len(landings) == 2


def test_a_windows_write_carries_every_batch(tmp_path: Any, landings: list[pl.DataFrame]) -> None:
    ingest_gp(
        _settings(tmp_path, batch_size=2),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 6),
        [1, 2, 3, 4, 5, 6],
        window_days=5,
        client=FakeSpaceTrack(),
    )

    (written,) = landings
    assert written["norad_id"].unique().sort().to_list() == [1, 2, 3, 4, 5, 6]
    assert written.height == 6 * 5


def test_the_combined_frame_is_sorted_deterministically(
    tmp_path: Any, landings: list[pl.DataFrame]
) -> None:
    """Concatenating batches must not break the ordering that byte-identical
    re-runs depend on."""
    ingest_gp(
        _settings(tmp_path, batch_size=2),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 4),
        [5, 6, 1, 2, 3, 4],
        window_days=3,
        client=FakeSpaceTrack(),
    )

    (written,) = landings
    assert written.equals(written.sort(["norad_id", "epoch", "gp_id"]))
    assert written.columns == list(gp_schema.BRONZE_COLUMNS)


# -- failure handling ------------------------------------------------------


def test_a_failed_batch_does_not_lose_the_rest_of_the_window(
    tmp_path: Any, landings: list[pl.DataFrame]
) -> None:
    client = FakeSpaceTrack(fail_on={2})

    report = ingest_gp(
        _settings(tmp_path, batch_size=2),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 4),
        [1, 2, 3, 4, 5, 6],
        window_days=3,
        client=client,
    )

    assert report.chunks_failed == 1
    assert len(landings) == 1
    # The two surviving batches still land.
    assert landings[0]["norad_id"].unique().sort().to_list() == [1, 2, 5, 6]


def test_continue_on_error_false_raises(tmp_path: Any, landings: list[pl.DataFrame]) -> None:
    with pytest.raises(RuntimeError, match="space-track said no"):
        ingest_gp(
            _settings(tmp_path, batch_size=2),
            dt.date(2024, 1, 1),
            dt.date(2024, 1, 4),
            [1, 2, 3, 4],
            window_days=3,
            client=FakeSpaceTrack(fail_on={1}),
            continue_on_error=False,
        )


def test_a_window_with_no_successful_batch_writes_nothing(
    tmp_path: Any, landings: list[pl.DataFrame]
) -> None:
    report = ingest_gp(
        _settings(tmp_path, batch_size=2),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 4),
        [1, 2],
        window_days=3,
        client=FakeSpaceTrack(fail_on={1}),
    )

    assert landings == []
    assert report.rows_written == 0
    assert report.chunks_failed == 1


def test_a_client_passed_in_is_not_closed_by_the_caller(
    tmp_path: Any, landings: list[pl.DataFrame]
) -> None:
    """The caller owns a client it supplied; ingest must not close it."""
    client = FakeSpaceTrack()

    ingest_gp(
        _settings(tmp_path),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 2),
        [1],
        window_days=1,
        client=client,
    )

    assert client.closed is False


def test_report_describes_itself() -> None:
    report = GpIngestReport(
        requests=4, rows_written=1234, rows_quarantined=2, partitions=90, chunks_failed=1
    )

    text = report.describe()
    assert "1,234 rows" in text
    assert "90 partitions" in text
    assert "FAILED" in text


# -- silently empty windows ------------------------------------------------


class EmptySpaceTrack(FakeSpaceTrack):
    """Returns HTTP-200-with-no-rows, the way a throttled Space-Track does."""

    def __init__(self, empty_after: int) -> None:
        super().__init__()
        self.empty_after = empty_after

    def gp_history(
        self, norad_ids: Sequence[int], start: dt.date, end: dt.date
    ) -> list[dict[str, Any]]:
        rows = super().gp_history(norad_ids, start, end)
        return [] if len(self.calls) > self.empty_after else rows


def test_a_window_that_returns_nothing_is_recorded_not_shrugged_at(
    tmp_path: Any, landings: list[pl.DataFrame]
) -> None:
    """Space-Track answers a throttled request with 200 and an empty array.

    A real backfill lost the last quarter of 2020 this way while reporting
    success, so an empty window is now recorded rather than skipped silently.
    """
    report = ingest_gp(
        _settings(tmp_path, batch_size=2),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 7),
        [1, 2],
        window_days=3,
        client=EmptySpaceTrack(empty_after=1),
    )

    assert report.empty_windows == ("2024-01-04..2024-01-07",)
    assert report.rows_written > 0, "the first window still landed"
    assert report.has_suspicious_gap, "an empty window beside a full one is a gap"


def test_an_entirely_empty_run_is_not_called_suspicious(
    tmp_path: Any, landings: list[pl.DataFrame]
) -> None:
    """A backfill starting before the first launch is legitimately empty, and
    must not be failed for it."""
    report = ingest_gp(
        _settings(tmp_path, batch_size=2),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 4),
        [1, 2],
        window_days=3,
        client=EmptySpaceTrack(empty_after=0),
    )

    assert report.rows_written == 0
    assert report.empty_windows
    assert not report.has_suspicious_gap


# -- bounded memory --------------------------------------------------------


def test_writes_are_bounded_by_rows_not_by_the_window(
    tmp_path: Any, landings: list[pl.DataFrame]
) -> None:
    """A 90-day window of 2025's constellation ran a 16 GB machine out of
    memory. Writes now happen whenever enough rows are collected."""
    ingest_gp(
        _settings(tmp_path, batch_size=2),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 11),
        [1, 2, 3, 4, 5, 6],  # 3 batches x 2 satellites x 10 days = 20 rows each
        window_days=10,
        max_rows_per_write=30,
        client=FakeSpaceTrack(),
    )

    assert [frame.height for frame in landings] == [40, 20]


def test_a_small_window_is_still_one_write(tmp_path: Any, landings: list[pl.DataFrame]) -> None:
    """The budget only splits windows that exceed it; small ones keep one write."""
    ingest_gp(
        _settings(tmp_path, batch_size=2),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 11),
        [1, 2, 3, 4, 5, 6],
        window_days=10,
        client=FakeSpaceTrack(),
    )

    assert [frame.height for frame in landings] == [60]


# -- resuming a retry ------------------------------------------------------


def test_a_retry_skips_the_windows_the_failed_attempt_landed(
    tmp_path: Any, landings: list[pl.DataFrame]
) -> None:
    """On 2026-09-26 a retry re-requested every window from the start of the
    range. Each request counts against Space-Track's limit; landed windows must
    not be fetched twice by the same run."""
    checkpoint = tmp_path / "checkpoint.json"
    arguments: dict[str, Any] = {
        "settings": _settings(tmp_path, batch_size=2),
        "start": dt.date(2024, 1, 1),
        "end": dt.date(2024, 1, 7),
        "norad_ids": [1, 2, 3, 4],
        "window_days": 3,
        "checkpoint": checkpoint,
        "continue_on_error": False,
    }
    # Window 1 is calls 1 and 2; the third call, in window 2, fails.
    with pytest.raises(RuntimeError):
        ingest_gp(**arguments, client=FakeSpaceTrack(fail_on={3}))

    retry = FakeSpaceTrack()
    ingest_gp(**arguments, client=retry)

    assert {start for _, start, _ in retry.calls} == {dt.date(2024, 1, 4)}


def test_a_window_with_a_failed_batch_is_not_recorded_as_finished(
    tmp_path: Any, landings: list[pl.DataFrame]
) -> None:
    checkpoint = tmp_path / "checkpoint.json"

    ingest_gp(
        _settings(tmp_path, batch_size=2),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 7),
        [1, 2, 3, 4],
        window_days=3,
        checkpoint=checkpoint,
        client=FakeSpaceTrack(fail_on={1}),
    )

    assert json.loads(checkpoint.read_text(encoding="utf-8")) == ["2024-01-04..2024-01-07"]


def test_an_empty_window_is_not_recorded_as_finished(
    tmp_path: Any, landings: list[pl.DataFrame]
) -> None:
    """Empty is what throttling looks like, so a retry must ask again."""
    checkpoint = tmp_path / "checkpoint.json"

    ingest_gp(
        _settings(tmp_path, batch_size=2),
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 4),
        [1, 2],
        window_days=3,
        checkpoint=checkpoint,
        client=EmptySpaceTrack(empty_after=0),
    )

    assert not checkpoint.exists()
