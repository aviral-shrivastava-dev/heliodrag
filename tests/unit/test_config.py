"""Settings come from the environment, and secrets stay out of reprs."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from starlink_drag.config import Settings, SpaceTrackSettings, get_settings


def test_defaults_carry_no_credentials() -> None:
    settings = Settings()

    assert settings.spacetrack.identity == ""
    assert settings.spacetrack.password.get_secret_value() == ""
    assert settings.lake.access_key_id.get_secret_value() == ""
    assert settings.spacetrack.is_configured is False


def test_environment_overrides_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPACETRACK_IDENTITY", "someone@example.org")
    monkeypatch.setenv("SPACETRACK_PASSWORD", "not-a-real-password")  # pragma: allowlist secret
    monkeypatch.setenv("PIPELINE_START_DATE", "2019-11-11")
    monkeypatch.setenv("LAKE_BACKEND", "r2")

    settings = Settings()

    assert settings.spacetrack.identity == "someone@example.org"
    assert settings.spacetrack.is_configured is True
    assert settings.pipeline_start_date == dt.date(2019, 11, 11)
    assert settings.lake.backend == "r2"


def test_secrets_are_not_exposed_by_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "a-password-that-must-never-reach-a-log"  # pragma: allowlist secret
    monkeypatch.setenv("SPACETRACK_PASSWORD", secret)

    assert secret not in repr(Settings())


def test_rate_limits_cannot_exceed_the_published_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Space-Track publishes <30/min and <300/hr; config must not go past them."""
    monkeypatch.setenv("SPACETRACK_REQUESTS_PER_MINUTE", "60")

    with pytest.raises(ValidationError):
        SpaceTrackSettings()


def test_unknown_lake_backend_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAKE_BACKEND", "gcs")

    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
