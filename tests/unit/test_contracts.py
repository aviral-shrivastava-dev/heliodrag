"""Upstream contract checks. No network -- the responses are served locally.

These exist because schema drift is silent: a renamed field produces nulls, not
an error, and every downstream number keeps being computed from emptier data
while the pipeline stays green. The check has to fail loudly on a shape change,
and has to keep quiet when there is nothing to check.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from starlink_drag import contracts
from starlink_drag.config import HapiSettings, Settings, SpaceTrackSettings

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _settings(**spacetrack: Any) -> Settings:
    base: dict[str, Any] = {
        "identity": "someone@example.org",
        "password": SecretStr("not-a-real-password"),  # pragma: allowlist secret
    }
    base.update(spacetrack)
    settings = Settings()
    object.__setattr__(settings, "spacetrack", SpaceTrackSettings(**base))
    object.__setattr__(settings, "hapi", HapiSettings())
    return settings


# -- OMNI ------------------------------------------------------------------


def _omni_client(info: dict[str, Any]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=info)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.invalid")


def _real_info() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (FIXTURES / "hapi" / "omni_info.json").read_text(encoding="utf-8")
    )
    return payload


def test_omni_passes_when_every_parameter_is_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _omni_client(_real_info())
    monkeypatch.setattr(
        contracts,
        "HapiClient",
        lambda settings: __import__(
            "starlink_drag.clients.hapi", fromlist=["HapiClient"]
        ).HapiClient(settings, client=client),
    )

    result = contracts.check_omni(_settings())

    assert result.ok
    assert not result.missing
    assert "F10_INDEX1800" in result.observed


def test_omni_reports_drift_when_a_parameter_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure this exists for: NASA retires a parameter and nothing
    downstream complains, it just goes null."""
    info = _real_info()
    info["parameters"] = [p for p in info["parameters"] if p["name"] != "DST1800"]
    client = _omni_client(info)
    monkeypatch.setattr(
        contracts,
        "HapiClient",
        lambda settings: __import__(
            "starlink_drag.clients.hapi", fromlist=["HapiClient"]
        ).HapiClient(settings, client=client),
    )

    result = contracts.check_omni(_settings())

    assert not result.ok
    assert result.missing == ("DST1800",)
    assert "DRIFT" in result.describe()


# -- Space-Track -----------------------------------------------------------


class FakeSpaceTrack:
    def __init__(self, element: dict[str, Any] | None, catalogue: list[dict[str, Any]]):
        self.element = element
        self.catalogue = catalogue
        self.asked_for: list[int] = []

    def gp_history(self, norad_ids: list[int], *args: Any) -> list[dict[str, Any]]:
        self.asked_for = list(norad_ids)
        return [self.element] if self.element else []

    def satcat(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return self.catalogue

    def close(self) -> None:
        pass

    def __enter__(self) -> FakeSpaceTrack:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeSpaceTrack) -> None:
    monkeypatch.setattr(contracts, "SpaceTrackClient", lambda settings: fake)


def _full_element() -> dict[str, Any]:
    from starlink_drag.schemas import gp

    return dict.fromkeys(gp.FIELD_MAP, "1")


def _entry(norad_id: int, launch: str, decay: str | None = None) -> dict[str, Any]:
    """A catalogue row with every field we parse. Only the three the probe
    choice reads carry meaningful values."""
    from starlink_drag.schemas import satcat

    row: dict[str, Any] = dict.fromkeys(satcat.FIELD_MAP, "1")
    row.update(NORAD_CAT_ID=str(norad_id), LAUNCH=launch, DECAY=decay)
    return row


def _full_catalogue() -> list[dict[str, Any]]:
    return [_entry(100, "2020-01-01")]


# -- choosing probes -------------------------------------------------------

TODAY = dt.date(2026, 9, 26)


def test_a_decayed_satellite_is_never_a_probe() -> None:
    """The bug this replaced: the check probed STARLINK-1007, which re-entered
    on 2024-10-02, and reported drift every night because it had no elements."""
    catalogue = [
        _entry(44713, "2019-11-11", decay="2024-10-02"),
        _entry(50000, "2022-01-01"),
    ]

    assert contracts.choose_probes(catalogue, TODAY) == [50000]


def test_probes_are_the_newest_satellites_on_orbit() -> None:
    catalogue = [_entry(n, f"2025-0{n}-01") for n in range(1, 8)]

    assert contracts.choose_probes(catalogue, TODAY, count=3) == [7, 6, 5]


def test_a_launch_from_the_last_month_is_not_a_probe() -> None:
    """The newest satellites share one launch, and a launch days old may not
    have a week of elements -- they would all come back empty together."""
    catalogue = [_entry(1, "2026-09-20"), _entry(2, "2026-08-01")]

    assert contracts.choose_probes(catalogue, TODAY) == [2]


def test_a_catalogue_row_without_a_launch_date_is_not_a_probe() -> None:
    assert contracts.choose_probes([_entry(1, "")], TODAY) == []


# -- Space-Track -----------------------------------------------------------


def test_spacetrack_skips_cleanly_without_credentials() -> None:
    """A fork or a contributor without an account gets a skip, not a red build."""
    result = contracts.check_spacetrack(_settings(identity="", password=SecretStr("")))

    assert result.skipped
    assert result.ok, "a skip is not a failure"
    assert "SKIP" in result.describe()


def test_spacetrack_passes_when_every_field_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, FakeSpaceTrack(_full_element(), _full_catalogue()))

    result = contracts.check_spacetrack(_settings())

    assert result.ok
    assert not result.missing
    assert result.describe().startswith("ok")


def test_spacetrack_asks_for_elements_of_the_chosen_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSpaceTrack(
        _full_element(),
        [_entry(44713, "2019-11-11", decay="2024-10-02"), _entry(60000, "2024-06-01")],
    )
    _install(monkeypatch, fake)

    contracts.check_spacetrack(_settings())

    assert fake.asked_for == [60000]


def test_spacetrack_reports_a_renamed_element_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    element = _full_element()
    del element["MEAN_MOTION"]
    _install(monkeypatch, FakeSpaceTrack(element, _full_catalogue()))

    result = contracts.check_spacetrack(_settings())

    assert not result.ok
    assert "gp_history.MEAN_MOTION" in result.missing
    assert "DRIFT" in result.describe()


def test_spacetrack_reports_a_renamed_catalogue_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogue = _full_catalogue()
    del catalogue[0]["RCS_SIZE"]
    _install(monkeypatch, FakeSpaceTrack(_full_element(), catalogue))

    result = contracts.check_spacetrack(_settings())

    assert not result.ok
    assert "satcat.RCS_SIZE" in result.missing


def test_no_elements_at_all_is_a_failure_not_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty response is how a throttled Space-Track answers, so it must not
    be read as 'the shape is fine' -- nor reported as drift, which it is not."""
    _install(monkeypatch, FakeSpaceTrack(None, _full_catalogue()))

    result = contracts.check_spacetrack(_settings())

    assert not result.ok
    assert result.empty
    assert result.describe().startswith("EMPTY")
    assert "no elements" in result.detail


def test_an_empty_catalogue_is_a_failure_not_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSpaceTrack(_full_element(), [])
    _install(monkeypatch, fake)

    result = contracts.check_spacetrack(_settings())

    assert not result.ok
    assert result.empty
    assert fake.asked_for == [], "nothing to probe, so no element request"


def test_a_catalogue_with_nothing_on_orbit_is_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every Starlink decayed is not a thing that happens; DECAY changing
    meaning is. Reported as drift, not as an empty answer."""
    catalogue = [_entry(1, "2020-01-01", decay="2024-01-01")]
    _install(monkeypatch, FakeSpaceTrack(_full_element(), catalogue))

    result = contracts.check_spacetrack(_settings())

    assert not result.ok
    assert not result.empty
    assert result.describe().startswith("DRIFT")


# -- dispatch --------------------------------------------------------------


def test_an_unknown_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown source"):
        contracts.check(_settings(), "cassini")


def test_all_runs_every_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        contracts, "check_omni", lambda s: contracts.ContractResult("nasa.omni", True, "x")
    )
    monkeypatch.setattr(
        contracts, "check_spacetrack", lambda s: contracts.ContractResult("spacetrack", True, "y")
    )

    results = contracts.check(_settings(), "all")

    assert {r.source for r in results} == {"nasa.omni", "spacetrack"}
