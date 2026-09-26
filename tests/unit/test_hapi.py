"""HAPI client tests, run against real responses captured from SPDF.

The fixtures in ``tests/fixtures/hapi`` are genuine server output, not
hand-written. OMNI is public domain, so they can be committed as-is. No test
here touches the network.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest

from starlink_drag.clients.hapi import OMNI_PARAMETERS, HapiClient, HapiError
from starlink_drag.config import HapiSettings

FIXTURES = Path(__file__).parent.parent / "fixtures" / "hapi"


def _serving(data_file: str) -> HapiClient:
    """A client wired to the captured /info and a chosen /data response."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/info"):
            return httpx.Response(
                200,
                content=(FIXTURES / "omni_info.json").read_bytes(),
                headers={"content-type": "application/json"},
            )
        return httpx.Response(
            200,
            content=(FIXTURES / data_file).read_bytes(),
            headers={"content-type": "text/csv"},
        )

    return HapiClient(
        HapiSettings(),
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://example.invalid"
        ),
    )


# -- parameter ordering ----------------------------------------------------


def test_parameters_come_back_in_the_datasets_declared_order() -> None:
    """Out-of-order parameters make the server return error 1411, not data."""
    with _serving("omni_2024-05-10.csv") as client:
        assert client.ordered_parameters(OMNI_PARAMETERS) == [
            "Time",
            "F10_INDEX1800",
            "KP1800",
            "DST1800",
            "AP_INDEX1800",
        ]


def test_time_is_included_even_when_not_requested() -> None:
    with _serving("omni_2024-05-10.csv") as client:
        assert client.ordered_parameters(("DST1800",)) == ["Time", "DST1800"]


def test_an_undeclared_parameter_is_rejected_before_the_request() -> None:
    with _serving("omni_2024-05-10.csv") as client:
        with pytest.raises(HapiError, match="does not declare"):
            client.ordered_parameters(("NOT_A_PARAMETER",))


# -- parsing ---------------------------------------------------------------


def test_real_storm_data_parses_to_hourly_rows() -> None:
    """10-11 May 2024: the Gannon storm, the strongest of Solar Cycle 25."""
    with _serving("omni_2024-05-10.csv") as client:
        rows = client.omni(dt.date(2024, 5, 10), dt.date(2024, 5, 12))

    assert len(rows) == 48
    assert rows[0]["Time"] == "2024-05-10T00:30:00.000Z"
    assert rows[0]["F10_INDEX1800"] == pytest.approx(227.9)

    # Dst collapses to -406 nT, the deepest of Solar Cycle 25, and Ap spikes.
    worst = min(rows, key=lambda r: r["DST1800"])
    assert worst["DST1800"] == pytest.approx(-406.0)
    assert worst["AP_INDEX1800"] >= 150


def test_fill_values_become_none_rather_than_numbers() -> None:
    """OMNI writes 999.9 for "no F10.7 measurement". Letting that through
    would put a fabricated solar flux into the science."""
    with _serving("omni_1963-01-01_with_fills.csv") as client:
        rows = client.omni(dt.date(1963, 1, 1), dt.date(1963, 1, 2))

    assert rows[0]["F10_INDEX1800"] is None
    assert all(r["F10_INDEX1800"] is None for r in rows)
    # Real measurements in the same rows survive.
    assert rows[0]["DST1800"] == pytest.approx(-6.0)


def test_kp_is_returned_as_the_servers_tenths_integer() -> None:
    """OMNI encodes Kp x10; converting it is the transformation layer's job,
    so bronze keeps what the server sent.

    The Gannon storm saturated the scale: Kp 9.0 arrives as 90, not 9.
    """
    with _serving("omni_2024-05-10.csv") as client:
        rows = client.omni(dt.date(2024, 5, 10), dt.date(2024, 5, 12))

    assert max(r["KP1800"] for r in rows) == pytest.approx(90.0)


# -- failure modes ---------------------------------------------------------


def test_an_error_body_served_with_http_200_is_detected() -> None:
    """HAPI reports failures in the body while the transport says success."""
    with _serving("omni_error_1411.json") as client:
        with pytest.raises(HapiError, match="status 1411"):
            client.omni(dt.date(2024, 5, 10), dt.date(2024, 5, 11))


def test_a_range_with_no_data_yet_is_empty_not_an_error() -> None:
    """NASA publishes OMNI about a week behind, and answers a request for a
    recent day with status 1201, "OK - no data for time range". Captured from
    the live server for 2026-09-24. Raising on it failed every daily run."""
    with _serving("omni_no_data_1201.csv") as client:
        assert client.omni(dt.date(2026, 9, 24), dt.date(2026, 9, 25)) == []


def test_a_non_200_from_info_is_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = HapiClient(
        HapiSettings(),
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://example.invalid"
        ),
    )
    with client, pytest.raises(HapiError, match="returned 503"):
        client.info()


def test_a_row_with_the_wrong_column_count_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/info"):
            return httpx.Response(200, content=(FIXTURES / "omni_info.json").read_bytes())
        return httpx.Response(200, text="2024-05-10T00:30:00.000Z,227.9\n")

    client = HapiClient(
        HapiSettings(),
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://example.invalid"
        ),
    )
    with client, pytest.raises(HapiError, match="expected 5 columns"):
        client.omni(dt.date(2024, 5, 10), dt.date(2024, 5, 11))
