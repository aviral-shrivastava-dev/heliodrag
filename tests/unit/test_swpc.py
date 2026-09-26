"""The SWPC client, against responses captured from the live service."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest
import respx

from starlink_drag.clients import swpc


def _load(fixtures_dir: Path, name: str) -> object:
    return json.loads((fixtures_dir / "swpc" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("product", "fixture", "quantities"),
    [
        (swpc.KP_1M, "planetary_k_index_1m.json", {"kp_estimated"}),
        (swpc.KP_3H, "noaa-planetary-k-index.json", {"kp", "ap"}),
        (swpc.DST, "kyoto-dst.json", {"dst"}),
        (swpc.F107, "10cm-flux-30-day.json", {"f107"}),
    ],
)
def test_every_product_parses_cleanly(
    fixtures_dir: Path, product: swpc.Product, fixture: str, quantities: set[str]
) -> None:
    observations, rejections = swpc.parse(product, _load(fixtures_dir, fixture))

    assert observations
    assert rejections == []
    assert {o.quantity for o in observations} == quantities
    assert all(o.observed_at.tzinfo is dt.UTC for o in observations), "SWPC time tags are UTC"


def test_the_three_hour_product_gives_kp_and_ap_for_the_same_interval(fixtures_dir: Path) -> None:
    observations, _ = swpc.parse(swpc.KP_3H, _load(fixtures_dir, "noaa-planetary-k-index.json"))
    first = [o for o in observations if o.observed_at == observations[0].observed_at]

    assert {o.quantity for o in first} == {"kp", "ap"}


def test_a_physically_impossible_value_is_rejected_with_a_reason() -> None:
    payload = [
        {"time_tag": "2026-09-26T12:00:00", "Kp": 12.0, "a_running": 7},
        {"time_tag": "2026-09-26T15:00:00", "Kp": 2.0, "a_running": 7},
    ]

    observations, rejections = swpc.parse(swpc.KP_3H, payload)

    assert [(o.quantity, o.value) for o in observations] == [("ap", 7.0), ("kp", 2.0), ("ap", 7.0)]
    assert len(rejections) == 1
    assert "kp 12.0 outside" in rejections[0].reason


def test_a_fill_value_is_not_a_solar_flux() -> None:
    _, rejections = swpc.parse(swpc.F107, [{"time_tag": "2026-09-25T20:00:00", "flux": 999.9}])

    assert len(rejections) == 1


def test_unreadable_records_are_rejected_not_raised() -> None:
    observations, rejections = swpc.parse(
        swpc.DST,
        [
            {"dst": -20},
            {"time_tag": "not a time", "dst": -20},
            "text",
            {"time_tag": "2026-09-26T01:00:00", "dst": "n/a"},
        ],
    )

    assert observations == []
    assert len(rejections) == 4


def test_a_null_value_is_skipped_quietly() -> None:
    observations, rejections = swpc.parse(
        swpc.KP_3H, [{"time_tag": "2026-09-26T12:00:00", "Kp": 2.0, "a_running": None}]
    )

    assert [o.quantity for o in observations] == ["kp"]
    assert rejections == []


def test_a_renamed_field_is_rejected_not_silently_empty() -> None:
    """Schema drift: if SWPC renamed `dst`, every record would parse to nothing."""
    observations, rejections = swpc.parse(
        swpc.DST, [{"time_tag": "2026-09-26T01:00:00", "dst_nt": -20}]
    )

    assert observations == []
    assert rejections[0].reason == "no dst field"


def test_a_response_that_is_not_an_array_is_one_rejection() -> None:
    observations, rejections = swpc.parse(swpc.DST, {"error": "maintenance"})

    assert observations == []
    assert rejections[0].reason == "expected a JSON array"


def test_an_observation_key_identifies_a_repeat() -> None:
    at = dt.datetime(2026, 9, 26, 12, tzinfo=dt.UTC)

    assert swpc.Observation("kp", at, 2.0).key == swpc.Observation("kp", at, 2.0).key
    assert swpc.Observation("kp", at, 2.0).key != swpc.Observation("ap", at, 2.0).key


@respx.mock
def test_fetch_reads_the_product_url(fixtures_dir: Path) -> None:
    route = respx.get("https://swpc.example/products/kyoto-dst.json").mock(
        return_value=httpx.Response(200, json=_load(fixtures_dir, "kyoto-dst.json"))
    )

    observations, _ = swpc.fetch("https://swpc.example/", swpc.DST)

    assert route.called
    assert observations


@respx.mock
def test_fetch_raises_on_a_server_error() -> None:
    respx.get("https://swpc.example/products/kyoto-dst.json").mock(return_value=httpx.Response(503))

    with pytest.raises(httpx.HTTPStatusError):
        swpc.fetch("https://swpc.example", swpc.DST)
