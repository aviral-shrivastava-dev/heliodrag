"""The rate limit holds across processes, not just within one.

The limiter's arithmetic -- never more than N starts in any rolling window -- is
tested in test_spacetrack.py against an in-memory ledger and a fake clock. These
tests are about *where* the history lives. On 2026-09-26 a Dagster retry ran as
a new process whose limiter remembered nothing, and together the two attempts
sent about 350 requests in half an hour against a limit of 300 an hour. The
ledger file is what stops that.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from starlink_drag.clients.rate_limit import (
    RateLimitWindow,
    SlidingWindowRateLimiter,
    SqliteLedger,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _limiter(ledger: SqliteLedger, clock: FakeClock, limit: int = 3) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(
        [RateLimitWindow(limit, 60.0)], ledger=ledger, clock=clock.time, sleep=clock.sleep
    )


def test_a_second_limiter_on_the_same_ledger_sees_the_first_ones_requests(
    tmp_path: Path,
) -> None:
    """Two limiters stand in for two processes: a retry and the attempt it replaced."""
    clock = FakeClock()
    ledger_file = tmp_path / "ledger.sqlite"
    first = _limiter(SqliteLedger(ledger_file), clock)
    for _ in range(3):
        assert first.acquire() == 0.0

    retry = _limiter(SqliteLedger(ledger_file), clock)

    assert retry.acquire() == pytest.approx(60.0), "it must wait for the first's minute"


def test_separate_ledgers_do_not_share_a_budget(tmp_path: Path) -> None:
    """The control for the test above: the sharing comes from the file."""
    clock = FakeClock()
    first = _limiter(SqliteLedger(tmp_path / "a.sqlite"), clock)
    for _ in range(3):
        first.acquire()

    other = _limiter(SqliteLedger(tmp_path / "b.sqlite"), clock)

    assert other.acquire() == 0.0


def test_old_requests_are_forgotten_once_outside_every_window(tmp_path: Path) -> None:
    clock = FakeClock()
    ledger = SqliteLedger(tmp_path / "ledger.sqlite")
    limiter = _limiter(ledger, clock)
    for _ in range(3):
        limiter.acquire()

    clock.now += 61.0

    assert limiter.acquire() == 0.0
    with ledger.transaction(clock.now - 60.0) as recent:
        assert len(recent.starts) == 1, "the three expired starts were pruned"


def test_a_decision_that_fails_records_nothing(tmp_path: Path) -> None:
    ledger = SqliteLedger(tmp_path / "ledger.sqlite")

    with pytest.raises(RuntimeError), ledger.transaction(0.0) as recent:
        recent.record(5.0)
        raise RuntimeError("interrupted mid-decision")

    with ledger.transaction(0.0) as recent:
        assert recent.starts == []


def test_the_limit_holds_between_two_real_processes(tmp_path: Path) -> None:
    """The real thing: another process takes the whole budget, and this one
    has to wait for it to age out. Real clocks, a two-second window."""
    ledger_file = tmp_path / "ledger.sqlite"
    other_process = textwrap.dedent(
        f"""
        from pathlib import Path
        from starlink_drag.clients.rate_limit import (
            RateLimitWindow, SlidingWindowRateLimiter, SqliteLedger,
        )
        limiter = SlidingWindowRateLimiter(
            [RateLimitWindow(3, 2.0)], ledger=SqliteLedger(Path({str(ledger_file)!r}))
        )
        for _ in range(3):
            limiter.acquire()
        """
    )
    subprocess.run([sys.executable, "-c", other_process], check=True, timeout=60)

    limiter = SlidingWindowRateLimiter([RateLimitWindow(3, 2.0)], ledger=SqliteLedger(ledger_file))
    started = time.time()
    limiter.acquire()

    assert time.time() - started > 0.5, "this process should have waited for the other's"
