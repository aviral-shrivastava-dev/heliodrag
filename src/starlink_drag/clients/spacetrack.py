"""Space-Track.org client.

This is the only module that talks to Space-Track, and the only place a rate
limiter for it exists. Space-Track publishes two simultaneous limits -- fewer
than 30 requests per minute and fewer than 300 per hour -- and enforces them by
blocking accounts, so the limiter is not advisory and is applied before the
request leaves, not after a rejection comes back.

Data obtained here is covered by a US-government data-use agreement that does
not permit redistribution. Nothing fetched by this module may be committed.
"""

from __future__ import annotations

import datetime as dt
import random
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import httpx

from starlink_drag.config import SpaceTrackSettings

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_LOGIN_PATH = "/ajaxauth/login"
_QUERY_PATH = "/basicspacedata/query"


class SpaceTrackError(RuntimeError):
    """Any failure talking to Space-Track."""


class SpaceTrackAuthError(SpaceTrackError):
    """Credentials were missing, rejected, or the session expired."""


@dataclass(frozen=True, slots=True)
class RateLimitWindow:
    """At most ``limit`` requests may start within any ``seconds``-long window."""

    limit: int
    seconds: float

    def __post_init__(self) -> None:
        if self.limit < 1 or self.seconds <= 0:
            raise ValueError("a rate-limit window needs a positive limit and duration")


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

    The clock and sleep function are injected so tests can drive it in
    microseconds rather than minutes.
    """

    def __init__(
        self,
        windows: Sequence[RateLimitWindow],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not windows:
            raise ValueError("at least one window is required")
        self._windows = tuple(windows)
        self._clock = clock
        self._sleep = sleep
        self._starts: deque[float] = deque()
        self._capacity = max(w.limit for w in self._windows)

    def _wait_needed(self, now: float) -> float:
        """Seconds to wait before another request may start, 0 if none."""
        wait = 0.0
        for window in self._windows:
            cutoff = now - window.seconds
            in_window = [t for t in self._starts if t > cutoff]
            if len(in_window) >= window.limit:
                # The oldest request that must age out before there is room.
                oldest = in_window[-window.limit]
                wait = max(wait, oldest + window.seconds - now)
        return wait

    def acquire(self) -> float:
        """Block until a request may start. Returns how long it waited."""
        waited = 0.0
        while True:
            now = self._clock()
            self._prune(now)
            wait = self._wait_needed(now)
            if wait <= 0:
                self._starts.append(now)
                if len(self._starts) > self._capacity:
                    self._starts.popleft()
                return waited
            self._sleep(wait)
            waited += wait

    def _prune(self, now: float) -> None:
        longest = max(w.seconds for w in self._windows)
        cutoff = now - longest
        while self._starts and self._starts[0] <= cutoff:
            self._starts.popleft()


def batched(items: Sequence[int], size: int) -> Iterator[list[int]]:
    """Split NORAD IDs into batches for comma-delimited querying.

    Space-Track accepts a comma-delimited list in a single request, so batching
    is the difference between one request per satellite and one per few hundred.
    """
    if size < 1:
        raise ValueError("batch size must be positive")
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


class SpaceTrackClient:
    """Rate-limited, retrying Space-Track client.

    Usage::

        with SpaceTrackClient(settings) as client:
            rows = client.gp_history([44713, 44714], start, end)
    """

    def __init__(
        self,
        settings: SpaceTrackSettings,
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._jitter = jitter
        self._authenticated = False
        self._limiter = SlidingWindowRateLimiter(
            [
                RateLimitWindow(settings.requests_per_minute, 60.0),
                RateLimitWindow(settings.requests_per_hour, 3600.0),
            ],
            clock=clock,
            sleep=sleep,
        )
        self._client = client or httpx.Client(
            base_url=settings.base_url, timeout=120.0, follow_redirects=True
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- authentication ----------------------------------------------------

    def login(self) -> None:
        """Authenticate and keep the session cookie on the underlying client."""
        if not self._settings.is_configured:
            raise SpaceTrackAuthError(
                "Space-Track credentials are absent. Set SPACETRACK_IDENTITY and "
                "SPACETRACK_PASSWORD in .env; see .env.example."
            )
        response = self._send(
            "POST",
            _LOGIN_PATH,
            data={
                "identity": self._settings.identity,
                "password": self._settings.password.get_secret_value(),
            },
        )
        if response.status_code != 200 or "Failed" in response.text:
            raise SpaceTrackAuthError("Space-Track rejected the credentials")
        self._authenticated = True

    def _ensure_login(self) -> None:
        if not self._authenticated:
            self.login()

    # -- requests ----------------------------------------------------------

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One rate-limited request, retried with exponential backoff + jitter."""
        last_error: Exception | None = None
        for attempt in range(self._settings.max_retries + 1):
            self._limiter.acquire()
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:  # network-level failure
                last_error = exc
            else:
                if response.status_code not in _RETRYABLE_STATUS:
                    return response
                last_error = SpaceTrackError(
                    f"Space-Track returned {response.status_code} for {path}"
                )
                retry_after = _retry_after_seconds(response)
                if retry_after is not None:
                    self._sleep(retry_after)
                    continue

            if attempt < self._settings.max_retries:
                self._sleep(self._backoff(attempt))

        raise SpaceTrackError(
            f"Space-Track request to {path} failed after {self._settings.max_retries + 1} attempts"
        ) from last_error

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter, capped at 60 seconds.

        Full jitter rather than a fixed multiplier: when several batches fail at
        once, identical sleeps would send them back in lockstep.
        """
        ceiling = min(60.0, 2.0**attempt)
        return ceiling * self._jitter()

    def query(self, *segments: str) -> list[dict[str, Any]]:
        """Run a basicspacedata query and return its JSON rows.

        A session that expires mid-run is re-established once and the query
        retried. A full backfill takes hours, so an expiry partway through is
        expected rather than exceptional -- without this, every remaining chunk
        would fail as an auth error against a perfectly good account.
        """
        path = "/".join([_QUERY_PATH, *segments, "format", "json"])

        for attempt in range(2):
            self._ensure_login()
            response = self._send("GET", path)

            if _is_unauthenticated(response):
                self._authenticated = False
                if attempt == 0:
                    continue
                raise SpaceTrackAuthError("Space-Track session could not be renewed")

            if response.status_code != 200:
                raise SpaceTrackError(f"Space-Track returned {response.status_code} for {path}")
            payload = response.json()
            if not isinstance(payload, list):
                raise SpaceTrackError(f"expected a JSON array from {path}")
            return payload

        raise SpaceTrackError(f"Space-Track query to {path} did not complete")

    # -- data ---------------------------------------------------------------

    def gp_history(
        self,
        norad_ids: Sequence[int],
        start: dt.date,
        end: dt.date,
    ) -> list[dict[str, Any]]:
        """General perturbations elements for these objects over [start, end).

        Queried by epoch range per batch of NORAD IDs rather than one request
        per day: the rate limit makes the wide query the difference between
        minutes and days of wall clock.
        """
        rows: list[dict[str, Any]] = []
        for batch in batched(norad_ids, self._settings.norad_ids_per_request):
            rows.extend(
                self.query(
                    "class",
                    "gp_history",
                    "NORAD_CAT_ID",
                    ",".join(str(i) for i in batch),
                    "EPOCH",
                    f"{start.isoformat()}--{end.isoformat()}",
                    "orderby",
                    "NORAD_CAT_ID,EPOCH",
                )
            )
        return rows

    def satcat(self, *, name_pattern: str = "~~STARLINK") -> list[dict[str, Any]]:
        """Catalogue metadata: launch date, decay date, object type."""
        return self.query("class", "satcat", "OBJECT_NAME", name_pattern, "orderby", "NORAD_CAT_ID")


def _is_unauthenticated(response: httpx.Response) -> bool:
    """Did Space-Track answer as though we are not logged in?

    It does not reliably use 401. An expired session is also answered with 200
    and the HTML login page, which would otherwise surface as a JSON decode
    error rather than as the auth problem it is.
    """
    if response.status_code in (401, 403):
        return True
    if response.status_code != 200:
        return False
    content_type = response.headers.get("content-type", "")
    return "html" in content_type.lower()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Honour a numeric Retry-After header when the server sends one."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
