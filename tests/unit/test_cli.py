"""The CLI is the non-Dagster entry point; it must work with no credentials."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from starlink_drag import __version__
from starlink_drag.cli import app

runner = CliRunner()


def test_version_matches_package() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_config_emits_valid_json() -> None:
    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["lake"]["backend"] == "local"


def test_config_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "a-password-that-must-never-reach-a-terminal"  # pragma: allowlist secret
    monkeypatch.setenv("SPACETRACK_PASSWORD", secret)

    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    assert secret not in result.stdout


def test_doctor_fails_loudly_when_credentials_are_absent() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "space-track credentials" in result.stdout


@pytest.mark.parametrize("command", ["demo", "app", "docs-site", "data-dictionary"])
def test_the_serving_commands_are_registered(command: str) -> None:
    result = runner.invoke(app, [command, "--help"])

    assert result.exit_code == 0


def test_demo_without_credentials_says_how_to_get_them() -> None:
    """The first thing a newcomer runs. It must fail with directions, not a trace."""
    result = runner.invoke(app, ["demo"])

    assert result.exit_code == 1
    assert "space-track.org/auth/createAccount" in result.stdout


def test_demo_refuses_an_empty_window() -> None:
    result = runner.invoke(app, ["demo", "--days", "0"])

    assert result.exit_code != 0
