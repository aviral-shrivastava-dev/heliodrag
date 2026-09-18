"""Tests for the Space-Track loader's rate limiting and partition semantics.

These are the parts that decide whether an 8-hour backfill survives being
interrupted, and they cannot be tested against the live API without burning the
rate limit, so the clock and the filesystem are exercised directly instead.
"""

import datetime as dt
import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from starlink_drag.ingest import spacetrack  # noqa: E402


class FakeClock:
    """Monotonic clock whose only movement is what the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(spacetrack.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(spacetrack.time, "sleep", fake.sleep)
    return fake


def test_under_the_limit_never_sleeps(clock):
    limiter = spacetrack.RateLimiter(per_minute=20, per_hour=250)
    for _ in range(20):
        limiter.acquire()
    assert clock.sleeps == []


def test_minute_ceiling_forces_a_wait(clock):
    limiter = spacetrack.RateLimiter(per_minute=5, per_hour=250)
    for _ in range(5):
        limiter.acquire()

    limiter.acquire()  # the 6th within the same minute must block
    assert len(clock.sleeps) == 1
    assert 0 < clock.sleeps[0] <= 61


def test_hour_ceiling_is_enforced_independently(clock):
    """The per-hour cap must bite even when the per-minute cap never would.

    This is the failure that gets an account banned: pacing to 20/minute looks
    fine for the first three minutes and then quietly breaches 250/hour.
    """
    limiter = spacetrack.RateLimiter(per_minute=1_000, per_hour=10)
    for _ in range(10):
        limiter.acquire()
        clock.now += 1  # one request per second: never close to a minute limit

    assert clock.sleeps == []
    limiter.acquire()
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] > 3_000  # must wait out most of the hour window


def test_old_calls_leave_the_window(clock):
    limiter = spacetrack.RateLimiter(per_minute=2, per_hour=250)
    limiter.acquire()
    limiter.acquire()
    clock.now += 61  # both fall out of the minute window
    limiter.acquire()
    assert clock.sleeps == []


@pytest.fixture
def bronze(tmp_path, monkeypatch):
    monkeypatch.setattr(spacetrack, "BRONZE_GP", tmp_path / "gp_history")
    return tmp_path / "gp_history"


def _frame(rows=3):
    return pd.DataFrame({"NORAD_CAT_ID": range(rows), "EPOCH": ["2025-01-01"] * rows})


def test_partition_incomplete_until_success_marker(bronze):
    day = dt.date(2025, 1, 15)
    assert not spacetrack.is_complete(day)
    spacetrack.write_partition(day, _frame())
    assert spacetrack.is_complete(day)


def test_partition_without_marker_is_not_trusted(bronze):
    """A parquet left behind by an interrupted run must not count as complete."""
    day = dt.date(2025, 1, 15)
    directory = bronze / f"epoch_date={day.isoformat()}"
    directory.mkdir(parents=True)
    _frame().to_parquet(directory / "data.parquet", index=False)

    assert not spacetrack.is_complete(day)


def test_write_leaves_no_staging_file(bronze):
    day = dt.date(2025, 1, 15)
    spacetrack.write_partition(day, _frame())
    directory = bronze / f"epoch_date={day.isoformat()}"
    assert (directory / "data.parquet").exists()
    assert not (directory / "data.parquet.tmp").exists()


def test_rewrite_is_idempotent(bronze):
    day = dt.date(2025, 1, 15)
    spacetrack.write_partition(day, _frame(rows=3))
    spacetrack.write_partition(day, _frame(rows=5))

    directory = bronze / f"epoch_date={day.isoformat()}"
    assert len(pd.read_parquet(directory / "data.parquet")) == 5
    assert len(list(directory.glob("*.parquet"))) == 1


def test_empty_day_still_marks_complete(bronze):
    """A genuinely empty day must be recorded, or every re-run refetches it."""
    day = dt.date(2019, 1, 1)
    spacetrack.write_partition(day, pd.DataFrame(columns=["NORAD_CAT_ID", "EPOCH"]))
    assert spacetrack.is_complete(day)


def test_completed_days_round_trips(bronze):
    for day in (dt.date(2025, 1, 1), dt.date(2025, 1, 3)):
        spacetrack.write_partition(day, _frame())
    assert spacetrack.completed_days() == [dt.date(2025, 1, 1), dt.date(2025, 1, 3)]


def test_backfill_skips_completed_days(bronze, monkeypatch):
    """Re-running a partially finished backfill must not refetch finished days."""
    spacetrack.write_partition(dt.date(2025, 1, 1), _frame())

    fetched = []

    class StubClient:
        def gp_history_for_day(self, day, name_pattern="STARLINK"):
            fetched.append(day)
            return _frame()

    summary = spacetrack.backfill(
        dt.date(2025, 1, 1), dt.date(2025, 1, 4), client=StubClient()
    )

    assert fetched == [dt.date(2025, 1, 2), dt.date(2025, 1, 3)]
    assert summary["days_skipped"] == 1
    assert summary["days_fetched"] == 2
