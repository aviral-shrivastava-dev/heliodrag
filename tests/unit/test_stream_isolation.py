"""The batch path does not depend on the streaming path, in any way that could
change it. Phase 6's acceptance is that the batch path is untouched; these hold
the line mechanically rather than by review."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from starlink_drag import warehouse
from starlink_drag.stream.table import DATASET, NOWCAST_TABLE

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_loading_the_batch_pipeline_loads_no_streaming_code() -> None:
    """The batch image does not install the Kafka client; importing the Dagster
    definitions or the CLI must not need it."""
    probe = (
        "import sys, starlink_drag.definitions, starlink_drag.cli; "
        "print(sorted(m for m in sys.modules if m.startswith(('confluent_kafka', 'starlink_drag.stream'))))"
    )
    # From the repository, with the venv's scripts on PATH, as the project runs:
    # the Dagster definitions resolve dbt and its manifest from there.
    environment = dict(os.environ)
    environment["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), environment["PATH"]])
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:]

    assert result.stdout.strip() == "[]"


def test_the_nowcast_is_not_a_warehouse_view() -> None:
    """dbt never sees the nowcast: it is in its own dataset, and no warehouse
    view is built over it."""
    assert DATASET == "stream"
    assert NOWCAST_TABLE not in {*warehouse.VIEWS, *warehouse.VIEWS.values()}
