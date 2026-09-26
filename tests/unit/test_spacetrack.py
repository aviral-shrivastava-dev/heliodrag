"""Space-Track client tests. These never touch the network.

The rate limiter is the highest-stakes code in the project: getting it wrong
costs the account that holds six years of history. It is therefore driven by a
fake clock and asserted against the published limits directly, rather than
tested for "roughly the right sleep calls".
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from starlink_drag.clients.rate_limit import RateLimitWindow, SlidingWindowRateLimiter
from starlink_drag.clients.spacetrack import (
    SpaceTrackAuthError,
    SpaceTrackClient,
    SpaceTrackError,
    batched,
)
from starlink_drag.config import SpaceTrackSettings


class FakeClock:
    """A clock that only advances when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _limiter(clock: FakeClock, *windows: RateLimitWindow) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(list(windows), clock=clock.time, sleep=clock.sleep)


def _max_in_any_window(starts: list[float], span: float) -> int:
    """Largest number of starts falling inside any window of length `span`."""
    return max(
        (sum(1 for t in starts if start <= t < start + span) for start in starts),
        default=0,
    )


# -- the limiter -----------------------------------------------------------


def test_never_exceeds_a_rolling_minute() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, RateLimitWindow(29, 60.0))

    starts = []
    for _ in range(200):
        limiter.acquire()
        starts.append(clock.now)

    assert _max_in_any_window(starts, 60.0) <= 29


def test_never_exceeds_either_window_when_both_apply() -> None:
    """The real configuration: <30/min and <300/hr simultaneously."""
    clock = FakeClock()
    limiter = _limiter(clock, RateLimitWindow(29, 60.0), RateLimitWindow(299, 3600.0))

    starts = []
    for _ in range(700):
        limiter.acquire()
        starts.append(clock.now)

    assert _max_in_any_window(starts, 60.0) <= 29
    assert _max_in_any_window(starts, 3600.0) <= 299


def test_a_burst_is_allowed_while_the_window_is_clear() -> None:
    """Being provably safe must not mean being pointlessly slow."""
    clock = FakeClock()
    limiter = _limiter(clock, RateLimitWindow(29, 60.0))

    for _ in range(29):
        assert limiter.acquire() == 0.0
    assert clock.now == 0.0

    assert limiter.acquire() > 0.0


def test_the_oldest_request_ages_out_of_the_window() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, RateLimitWindow(2, 60.0))

    limiter.acquire()
    clock.now = 30.0
    limiter.acquire()
    limiter.acquire()

    # Two are in flight at t=30; the next must wait for the t=0 one to expire.
    assert clock.now == 60.0


def test_a_window_needs_a_positive_limit_and_duration() -> None:
    with pytest.raises(ValueError):
        RateLimitWindow(0, 60.0)
    with pytest.raises(ValueError):
        RateLimitWindow(29, 0.0)


# -- batching --------------------------------------------------------------


def test_norad_ids_are_batched_for_comma_delimited_queries() -> None:
    assert list(batched(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]


def test_batching_an_empty_list_yields_nothing() -> None:
    assert list(batched([], 10)) == []


def test_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        list(batched([1, 2], 0))


# -- the client ------------------------------------------------------------


def _settings(**overrides: Any) -> SpaceTrackSettings:
    base: dict[str, Any] = {
        "identity": "someone@example.org",
        "password": SecretStr("not-a-real-password"),  # pragma: allowlist secret
        "max_retries": 3,
        "norad_ids_per_request": 2,
    }
    base.update(overrides)
    return SpaceTrackSettings(**base)


def _client(
    handler: Any, clock: FakeClock | None = None, **setting_overrides: Any
) -> SpaceTrackClient:
    clock = clock or FakeClock()
    return SpaceTrackClient(
        _settings(**setting_overrides),
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://example.invalid"
        ),
        clock=clock.time,
        sleep=clock.sleep,
        jitter=lambda: 1.0,
    )


def test_login_is_required_before_a_query_and_happens_once() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("login"):
            return httpx.Response(200, text="")
        return httpx.Response(200, json=[{"NORAD_CAT_ID": "1"}])

    with _client(handler) as client:
        client.query("class", "satcat")
        client.query("class", "satcat")

    assert calls.count("/ajaxauth/login") == 1


def test_missing_credentials_fail_before_any_request_is_made() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be attempted without credentials")

    with _client(handler, identity="", password=SecretStr("")) as client:
        with pytest.raises(SpaceTrackAuthError, match="credentials are absent"):
            client.login()


def test_rejected_credentials_raise_an_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Failed: login")

    with _client(handler) as client, pytest.raises(SpaceTrackAuthError, match="rejected"):
        client.login()


def test_a_retryable_status_is_retried_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("login"):
            return httpx.Response(200, text="")
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=[{"ok": True}])

    with _client(handler) as client:
        assert client.query("class", "satcat") == [{"ok": True}]

    assert attempts["n"] == 3


def test_retries_are_bounded_and_then_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("login"):
            return httpx.Response(200, text="")
        return httpx.Response(500)

    with _client(handler, max_retries=2) as client:
        with pytest.raises(SpaceTrackError, match="failed after 3 attempts"):
            client.query("class", "satcat")


def test_backoff_grows_and_carries_jitter() -> None:
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("login"):
            return httpx.Response(200, text="")
        return httpx.Response(500)

    with _client(handler, clock=clock, max_retries=3) as client, pytest.raises(SpaceTrackError):
        client.query("class", "satcat")

    # jitter is pinned to 1.0, so sleeps are the ceilings: 1 + 2 + 4.
    assert clock.now == pytest.approx(7.0)


def test_a_numeric_retry_after_header_is_honoured() -> None:
    clock = FakeClock()
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("login"):
            return httpx.Response(200, text="")
        seen["n"] += 1
        if seen["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "12"})
        return httpx.Response(200, json=[])

    with _client(handler, clock=clock) as client:
        client.query("class", "satcat")

    assert clock.now == pytest.approx(12.0)


def test_gp_history_batches_norad_ids_into_comma_delimited_paths() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("login"):
            return httpx.Response(200, text="")
        paths.append(request.url.path)
        return httpx.Response(200, json=[{"NORAD_CAT_ID": "1"}])

    with _client(handler) as client:  # norad_ids_per_request=2
        rows = client.gp_history([44713, 44714, 44715], dt.date(2024, 5, 10), dt.date(2024, 5, 12))

    assert len(paths) == 2
    assert "44713,44714" in paths[0]
    assert "44715" in paths[1]
    assert "2024-05-10--2024-05-12" in paths[0]
    assert len(rows) == 2


def test_a_non_array_payload_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("login"):
            return httpx.Response(200, text="")
        return httpx.Response(200, json={"error": "nope"})

    with _client(handler) as client:
        with pytest.raises(SpaceTrackError, match="expected a JSON array"):
            client.query("class", "satcat")


# -- session expiry --------------------------------------------------------


def test_an_expired_session_is_renewed_once_and_the_query_succeeds() -> None:
    """A backfill runs for hours; the session will expire partway through."""
    logins = {"n": 0}
    queries = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("login"):
            logins["n"] += 1
            return httpx.Response(200, text="")
        queries["n"] += 1
        if queries["n"] == 1:
            # Space-Track answers an expired session with the HTML login page,
            # not with 401.
            return httpx.Response(
                200, text="<html>login</html>", headers={"content-type": "text/html"}
            )
        return httpx.Response(200, json=[{"ok": True}])

    with _client(handler) as client:
        assert client.query("class", "satcat") == [{"ok": True}]

    assert logins["n"] == 2
    assert queries["n"] == 2


def test_a_session_that_cannot_be_renewed_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("login"):
            return httpx.Response(200, text="")
        return httpx.Response(401)

    with _client(handler) as client:
        with pytest.raises(SpaceTrackAuthError, match="could not be renewed"):
            client.query("class", "satcat")
