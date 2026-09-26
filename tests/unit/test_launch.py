"""The one-command path from a fresh clone to a running explorer.

Nothing here reaches Space-Track, dbt or Streamlit: each step is replaced so
the sequencing, the stop-on-failure behaviour and the guidance a newcomer sees
can be checked on their own. The real run is the Phase 5 acceptance check.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from starlink_drag import backfill, launch
from starlink_drag.config import get_settings


def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPACETRACK_IDENTITY", "someone@example.org")
    monkeypatch.setenv("SPACETRACK_PASSWORD", "not-a-real-password")  # pragma: allowlist secret
    get_settings.cache_clear()


class Steps:
    """Records what the demo asked for, in order, instead of doing it."""

    def __init__(self, *, prepare: int = 0, backfill: int = 0) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._prepare = prepare
        self._backfill = backfill

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(launch, "prepare", self.prepare)
        monkeypatch.setattr(backfill, "last_partition_key", lambda: "2026-09-24")
        monkeypatch.setattr(backfill, "run", self.backfill)
        monkeypatch.setattr(launch, "serve", self.serve)

    def prepare(self, report: Any) -> int:
        self.calls.append(("prepare", None))
        return self._prepare

    def backfill(self, start: str, end: str, *, report: Any) -> int:
        self.calls.append(("backfill", (start, end)))
        return self._backfill

    def serve(self, port: int, report: Any) -> int:
        self.calls.append(("serve", port))
        return 0


# -- the window ------------------------------------------------------------


def test_the_window_ends_at_the_newest_partition() -> None:
    last = dt.date(2026, 9, 24)
    assert launch.window(last, 30) == (dt.date(2026, 8, 26), last)


def test_a_one_day_window_is_that_day() -> None:
    last = dt.date(2026, 9, 24)
    assert launch.window(last, 1) == (last, last)


def test_an_empty_window_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        launch.window(dt.date(2026, 9, 24), 0)


# -- without credentials ---------------------------------------------------


def test_without_credentials_it_explains_how_to_get_them(tmp_path: Path) -> None:
    lines: list[str] = []

    code = launch.demo(report=lines.append)

    assert code == 1
    assert "space-track.org/auth/createAccount" in lines[0]
    assert "SPACETRACK_IDENTITY" in lines[0]


def test_it_creates_env_from_the_example_for_a_newcomer(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("SPACETRACK_IDENTITY=\n", encoding="utf-8")
    lines: list[str] = []

    launch.demo(report=lines.append)

    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SPACETRACK_IDENTITY=\n"
    assert "Created .env from .env.example" in lines[0]


def test_it_never_overwrites_an_existing_env(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("SPACETRACK_IDENTITY=\n", encoding="utf-8")
    (tmp_path / ".env").write_text("# mine\n", encoding="utf-8")

    launch.demo(report=lambda line: None)

    assert (tmp_path / ".env").read_text(encoding="utf-8") == "# mine\n"


# -- with credentials ------------------------------------------------------


def test_the_demo_prepares_lands_the_window_then_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    steps = Steps()
    steps.install(monkeypatch)

    code = launch.demo(days=30, port=8765, report=lambda line: None)

    assert code == 0
    assert steps.calls == [
        ("prepare", None),
        ("backfill", ("2026-08-26", "2026-09-24")),
        ("serve", 8765),
    ]


@pytest.mark.parametrize("failing", ["prepare", "backfill"])
def test_a_failed_step_stops_the_demo_before_serving(
    monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    _configured(monkeypatch)
    steps = Steps(**{failing: 2})
    steps.install(monkeypatch)

    code = launch.demo(report=lambda line: None)

    assert code == 2
    assert "serve" not in [name for name, _ in steps.calls]


# -- serving ---------------------------------------------------------------


def test_serving_outside_the_repository_says_where_to_run_it() -> None:
    lines: list[str] = []

    assert launch.serve(report=lines.append) == 1
    assert "repository root" in lines[0]


def test_serving_runs_streamlit_with_this_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "streamlit_app.py").write_text("", encoding="utf-8")
    seen: list[list[str]] = []

    class Finished:
        returncode = 0

    def fake_run(command: list[str], check: bool) -> Finished:
        seen.append(command)
        return Finished()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert launch.serve(port=8765, report=lambda line: None) == 0
    assert seen == [
        [sys.executable, "-m", "streamlit", "run", str(launch.APP), "--server.port", "8765"]
    ]
