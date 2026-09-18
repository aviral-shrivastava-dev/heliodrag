"""Space-Track GP history loader: rate-limited, resumable, idempotent.

The orbital history is ~45M records behind an API capped at 30 requests/minute
and 300/hour. A full backfill is therefore an ~8-hour job, which means it *will*
be interrupted -- by a laptop sleeping, a network blip, or a 500 from the server.
The loader is built so that re-running it is always safe and always resumes.

Design
------
Partition per UTC day, written atomically:

    data/bronze/gp_history/epoch_date=2024-01-15/data.parquet
    data/bronze/gp_history/epoch_date=2024-01-15/_SUCCESS

A partition counts as complete only when `_SUCCESS` exists. Data is written to a
temp file and renamed into place, so an interrupted write never leaves a partial
parquet that looks complete. Re-running skips finished partitions, so the job is
idempotent and resumable with no external state store.

Queries use Space-Track's wildcard operator (`OBJECT_NAME/~~STARLINK`) to pull a
whole day for the entire constellation in one request. Per-satellite queries
would need ~12,000x more requests and could not finish inside the rate limit.
"""

from __future__ import annotations

import collections
import datetime as dt
import io
import logging
import time
import urllib.parse

import pandas as pd
import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .. import config

log = logging.getLogger(__name__)

BRONZE_GP = config.BRONZE / "gp_history"


class RateLimiter:
    """Sliding-window limiter enforcing per-minute and per-hour ceilings at once.

    Space-Track applies both limits simultaneously, so honouring only the
    per-minute one still earns a ban after ~15 minutes of steady querying.
    """

    def __init__(self, per_minute: int, per_hour: int) -> None:
        self.per_minute = per_minute
        self.per_hour = per_hour
        self._calls: collections.deque[float] = collections.deque()

    def _prune(self, now: float) -> None:
        while self._calls and now - self._calls[0] > 3600:
            self._calls.popleft()

    def acquire(self) -> None:
        """Block until a request may be issued without breaching either limit."""
        while True:
            now = time.monotonic()
            self._prune(now)
            in_last_minute = sum(1 for t in self._calls if now - t <= 60)

            if in_last_minute >= self.per_minute:
                oldest = next(t for t in self._calls if now - t <= 60)
                wait = 60 - (now - oldest) + 0.1
            elif len(self._calls) >= self.per_hour:
                wait = 3600 - (now - self._calls[0]) + 0.1
            else:
                self._calls.append(now)
                return

            log.info("rate limit reached, sleeping %.1fs", wait)
            time.sleep(wait)


class SpaceTrackError(RuntimeError):
    """Raised for retryable transport or server-side failures."""


class SpaceTrackClient:
    """Authenticated Space-Track session with shared rate limiting."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "starlink-drag-atlas/0.1 (academic research)"
        self._limiter = RateLimiter(
            config.SPACETRACK_MAX_PER_MINUTE, config.SPACETRACK_MAX_PER_HOUR
        )
        self._authenticated = False

    def login(self) -> None:
        credentials = config.spacetrack_credentials()
        self._limiter.acquire()
        response = self._session.post(
            f"{config.SPACETRACK_BASE}/ajaxauth/login",
            data={"identity": credentials.identity, "password": credentials.password},
            timeout=60,
        )
        if response.status_code != 200 or "Failed" in response.text[:200]:
            raise SpaceTrackError(f"Space-Track login failed ({response.status_code})")
        self._authenticated = True
        log.info("authenticated with Space-Track")

    @retry(
        retry=retry_if_exception_type(SpaceTrackError),
        wait=wait_exponential(multiplier=5, min=5, max=300),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, url: str) -> str:
        if not self._authenticated:
            self.login()

        self._limiter.acquire()
        response = self._session.get(url, timeout=300)

        if response.status_code == 401:
            # Session expired mid-backfill; re-authenticate and let retry re-issue.
            self._authenticated = False
            raise SpaceTrackError("session expired")
        if response.status_code == 429:
            raise SpaceTrackError("throttled by server")
        if response.status_code >= 500:
            raise SpaceTrackError(f"server error {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"unexpected status {response.status_code}: {response.text[:300]}")

        return response.text

    def gp_history_for_day(self, day: dt.date, name_pattern: str = "STARLINK") -> pd.DataFrame:
        """Fetch one UTC day of GP records for every object matching `name_pattern`."""
        start = day.isoformat()
        end = (day + dt.timedelta(days=1)).isoformat()
        predicates = ",".join(config.GP_PREDICATES)
        path = (
            f"/basicspacedata/query/class/gp_history"
            f"/OBJECT_NAME/~~{name_pattern}"
            f"/EPOCH/{start}--{end}"
            f"/predicates/{predicates}"
            f"/orderby/{urllib.parse.quote('EPOCH asc')}"
            f"/format/csv"
        )
        payload = self._get(f"{config.SPACETRACK_BASE}{path}")

        if not payload.strip():
            return pd.DataFrame(columns=config.GP_PREDICATES)
        return pd.read_csv(io.StringIO(payload))


def _partition_dir(day: dt.date):
    return BRONZE_GP / f"epoch_date={day.isoformat()}"


def is_complete(day: dt.date) -> bool:
    """A partition is complete only if its _SUCCESS marker exists."""
    return (_partition_dir(day) / "_SUCCESS").exists()


def write_partition(day: dt.date, frame: pd.DataFrame) -> int:
    """Write one day atomically, then mark it complete.

    Data lands in a temp file and is renamed, so an interrupted run can never
    leave behind a truncated parquet that a later run would treat as valid.
    """
    directory = _partition_dir(day)
    directory.mkdir(parents=True, exist_ok=True)

    final = directory / "data.parquet"
    staging = directory / "data.parquet.tmp"
    frame.to_parquet(staging, index=False)
    staging.replace(final)

    (directory / "_SUCCESS").write_text(
        f"{dt.datetime.now(dt.timezone.utc).isoformat()}\t{len(frame)}\n",
        encoding="utf-8",
    )
    return len(frame)


def backfill(
    start: dt.date,
    end: dt.date,
    client: SpaceTrackClient | None = None,
    refresh: bool = False,
) -> dict[str, int]:
    """Load every UTC day in [start, end), skipping partitions already complete.

    Safe to interrupt and re-run. Returns a summary of what this invocation did.
    """
    client = client or SpaceTrackClient()
    config.ensure_layers()

    days = [start + dt.timedelta(days=i) for i in range((end - start).days)]
    pending = [d for d in days if refresh or not is_complete(d)]

    log.info(
        "backfill %s..%s: %d days total, %d already complete, %d to fetch "
        "(~%.1f hours at %d req/hour)",
        start, end, len(days), len(days) - len(pending), len(pending),
        len(pending) / config.SPACETRACK_MAX_PER_HOUR, config.SPACETRACK_MAX_PER_HOUR,
    )

    summary = {"days_fetched": 0, "rows": 0, "days_skipped": len(days) - len(pending)}
    for day in pending:
        frame = client.gp_history_for_day(day)
        rows = write_partition(day, frame)
        summary["days_fetched"] += 1
        summary["rows"] += rows
        log.info("%s: %d rows (%d/%d)", day, rows, summary["days_fetched"], len(pending))

    return summary


def completed_days() -> list[dt.date]:
    """Every day already present in bronze, for progress reporting and watermarks."""
    if not BRONZE_GP.exists():
        return []
    days = []
    for directory in BRONZE_GP.iterdir():
        if (directory / "_SUCCESS").exists() and "=" in directory.name:
            days.append(dt.date.fromisoformat(directory.name.split("=", 1)[1]))
    return sorted(days)
