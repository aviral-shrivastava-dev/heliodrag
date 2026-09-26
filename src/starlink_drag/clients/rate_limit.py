"""The Space-Track rate limit, enforced across every process on this machine.

Space-Track publishes two simultaneous limits -- fewer than 30 requests per
minute and fewer than 300 per hour -- and enforces them by blocking accounts.
The limiter waits *before* a request leaves, so it cannot exceed a rolling
window by construction (ADR-0003).

Where it remembers recent requests matters as much as how it counts them. The
first version kept them in the memory of one process, and a pipeline run is
several processes: Dagster runs the catalogue and the element fetch as separate
steps, and retries a failed step as a new process. On 2026-09-26 a retry started
counting from zero while the failed attempt's requests were still inside
Space-Track's hour, and about 350 requests went out in half an hour.

So the record of recent requests now lives in a small SQLite file that every
process shares, and each decision is made under SQLite's write lock: one
process at a time reads the recent history, decides, and records its request
before the next may look. See ADR-0008.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RateLimitWindow:
    """At most ``limit`` requests may start within any ``seconds``-long window."""

    limit: int
    seconds: float

    def __post_init__(self) -> None:
        if self.limit < 1 or self.seconds <= 0:
            raise ValueError("a rate-limit window needs a positive limit and duration")


@dataclass(slots=True)
class LedgerView:
    """What one decision sees: recent start times, and what it adds to them."""

    starts: list[float]
    recorded: list[float] = field(default_factory=list)

    def record(self, when: float) -> None:
        self.recorded.append(when)


class MemoryLedger:
    """Start times held in this process only. For tests, which drive a fake clock."""

    def __init__(self) -> None:
        self._starts: list[float] = []

    @contextmanager
    def transaction(self, since: float) -> Iterator[LedgerView]:
        self._starts = [t for t in self._starts if t > since]
        view = LedgerView(list(self._starts))
        yield view
        self._starts.extend(view.recorded)


class SqliteLedger:
    """Start times in a SQLite file that every process on the machine shares.

    ``BEGIN IMMEDIATE`` takes SQLite's write lock before anything is read, so two
    processes can never both see room for one more request and both take it.
    A process waiting for the lock waits up to ``timeout`` seconds; the lock is
    held only for the few milliseconds a decision takes, never across a request.
    """

    def __init__(self, path: Path, *, timeout: float = 60.0) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._timeout = timeout
        with closing(self._connect()) as connection:
            connection.execute(
                "create table if not exists request_starts (started_at real not null)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=self._timeout, isolation_level=None)

    @contextmanager
    def transaction(self, since: float) -> Iterator[LedgerView]:
        with closing(self._connect()) as connection:
            connection.execute("begin immediate")
            try:
                connection.execute("delete from request_starts where started_at <= ?", (since,))
                starts = [
                    row[0]
                    for row in connection.execute(
                        "select started_at from request_starts order by started_at"
                    )
                ]
                view = LedgerView(starts)
                yield view
                connection.executemany(
                    "insert into request_starts (started_at) values (?)",
                    [(when,) for when in view.recorded],
                )
            except BaseException:
                connection.execute("rollback")
                raise
            connection.execute("commit")


class SlidingWindowRateLimiter:
    """Enforces several rolling-window limits at once.

    A token bucket sized to the limit is the more common choice, but it permits
    a full burst at the end of one window and another at the start of the next
    -- up to twice the published limit inside a single rolling window, which is
    exactly the pattern that gets a Space-Track account blocked.

    This keeps the start time of recent requests instead, and waits until the
    oldest one has aged out of every window. It cannot exceed a rolling limit by
    construction, and still allows a genuine burst when the window really is
    clear. See ADR-0003.

    The clock is wall time, not ``time.monotonic``: a monotonic clock means
    nothing outside the process that read it, and the ledger is shared. The
    clock and sleep function are injected so tests run in microseconds.
    """

    def __init__(
        self,
        windows: Sequence[RateLimitWindow],
        *,
        ledger: MemoryLedger | SqliteLedger | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not windows:
            raise ValueError("at least one window is required")
        self._windows = tuple(windows)
        self._ledger = ledger or MemoryLedger()
        self._clock = clock
        self._sleep = sleep
        self._horizon = max(w.seconds for w in self._windows)

    def _wait_needed(self, starts: list[float], now: float) -> float:
        """Seconds to wait before another request may start, 0 if none."""
        wait = 0.0
        for window in self._windows:
            cutoff = now - window.seconds
            in_window = [t for t in starts if t > cutoff]
            if len(in_window) >= window.limit:
                # The oldest request that must age out before there is room.
                oldest = in_window[-window.limit]
                wait = max(wait, oldest + window.seconds - now)
        return wait

    def acquire(self) -> float:
        """Block until a request may start, and record it. Returns seconds waited."""
        waited = 0.0
        while True:
            now = self._clock()
            with self._ledger.transaction(now - self._horizon) as recent:
                wait = self._wait_needed(recent.starts, now)
                if wait <= 0:
                    recent.record(now)
                    return waited
            self._sleep(wait)
            waited += wait
