"""Upstream contract checks. No network -- the responses are served locally.

These exist because schema drift is silent: a renamed field produces nulls, not
an error, and every downstream number keeps being computed from emptier data
while the pipeline stays green. The check has to fail loudly on a shape change,
and has to keep quiet when there is nothing to check.
"""

from __future__ import annotations

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
    def __init__(self, element: dict[str, Any] | None, catalogue: dict[str, Any] | None):
        self.element = element
        self.catalogue = catalogue

    def gp_history(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [self.element] if self.element else []

    def satcat(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [self.catalogue] if self.catalogue else []

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


def _full_catalogue() -> dict[str, Any]:
    from starlink_drag.schemas import satcat

    return dict.fromkeys(satcat.FIELD_MAP, "1")


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


def test_spacetrack_reports_a_renamed_element_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    element = _full_element()
    del element["MEAN_MOTION"]
    _install(monkeypatch, FakeSpaceTrack(element, _full_catalogue()))

    result = contracts.check_spacetrack(_settings())

    assert not result.ok
    assert "gp_history.MEAN_MOTION" in result.missing


def test_spacetrack_reports_a_renamed_catalogue_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogue = _full_catalogue()
    del catalogue["DECAY"]
    _install(monkeypatch, FakeSpaceTrack(_full_element(), catalogue))

    result = contracts.check_spacetrack(_settings())

    assert not result.ok
    assert "satcat.DECAY" in result.missing


def test_no_elements_at_all_is_a_failure_not_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty response is how a throttled Space-Track answers, so it must not
    be read as 'the shape is fine'."""
    _install(monkeypatch, FakeSpaceTrack(None, _full_catalogue()))

    result = contracts.check_spacetrack(_settings())

    assert not result.ok
    assert "no elements" in result.detail


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
