"""The explorer's live page, run headless against a nowcast table in a temporary
lake. No broker: the page reads only the table, so the table is what it needs.
The element sets are synthetic; see tests/fixtures/README.md."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from starlink_drag.clients.swpc import Observation
from starlink_drag.config import get_settings
from starlink_drag.stream.nowcast import ElementSet, NowcastState
from starlink_drag.stream.table import latest, read_nowcast, write_nowcast

pytestmark = pytest.mark.integration

PAGE = Path(__file__).resolve().parents[2] / "app" / "pages" / "live_nowcast.py"


@pytest.fixture
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    return tmp_path


def _snapshot(computed_at: dt.datetime) -> None:
    epoch = computed_at - dt.timedelta(hours=3)
    state = NowcastState({1: "v1.5", 2: "v2-mini"})
    for norad in (1, 2):
        state.add_element_set(ElementSet(norad, norad, epoch - dt.timedelta(hours=12), 15.06, 1e-4))
        state.add_element_set(ElementSet(norad, norad + 10, epoch, 15.061, 1.2e-4))
    state.add_observation(Observation("kp_estimated", computed_at, 2.33))
    write_nowcast(state.snapshot(computed_at), get_settings())


def test_the_page_shows_the_latest_nowcast(lake: Path) -> None:
    now = dt.datetime.now(dt.UTC)
    _snapshot(now - dt.timedelta(minutes=20))
    _snapshot(now - dt.timedelta(minutes=5))

    app = AppTest.from_file(str(PAGE), default_timeout=60).run()

    assert not app.exception, app.exception
    assert not app.warning, "a nowcast from five minutes ago is not stale"
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Kp now (estimated)"] == "2.33"
    table = app.dataframe[0].value
    assert list(table["Generation"]) == ["v1.5", "v2-mini"]
    assert (table["Median altitude change (m/day)"] < 0).all()
    assert len(read_nowcast(get_settings())) == 4
    assert len(latest(read_nowcast(get_settings()))) == 2


def test_an_old_nowcast_is_flagged_as_stale(lake: Path) -> None:
    _snapshot(dt.datetime.now(dt.UTC) - dt.timedelta(hours=3))

    app = AppTest.from_file(str(PAGE), default_timeout=60).run()

    assert not app.exception, app.exception
    assert "Is the consumer running?" in app.warning[0].value


def test_the_page_says_how_to_start_the_stream_when_there_is_no_nowcast(lake: Path) -> None:
    app = AppTest.from_file(str(PAGE), default_timeout=60).run()

    assert not app.exception, app.exception
    assert "--profile streaming" in app.info[0].value
