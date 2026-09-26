"""From a fresh clone to a running explorer, in one command.

``uv run starlink-drag demo`` does, in order:

1. checks for Space-Track credentials, and says exactly how to get them if not;
2. installs the dbt packages and parses the project, which Dagster needs before
   it can load the asset graph;
3. runs the production backfill -- the same Dagster assets, checks and dbt
   build as ``starlink-drag backfill`` -- over the most recent weeks only;
4. starts the explorer.

No data ships with the repository. Space-Track's user agreement forbids
redistributing it, so a newcomer ingests their own under their own free
account. Thirty days keeps that to minutes instead of the hours a full run
takes, and uses a small fraction of the hourly rate limit.
"""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

from starlink_drag import backfill
from starlink_drag.config import get_settings
from starlink_drag.dbt_runner import prepare

APP: Final = Path("app") / "streamlit_app.py"
DEFAULT_DAYS: Final = 30
DEFAULT_PORT: Final = 8501

Report = Callable[[str], None]


def window(last: dt.date, days: int) -> tuple[dt.date, dt.date]:
    """The ``days`` most recent daily partitions, ending with ``last``."""
    if days < 1:
        raise ValueError(f"days must be at least 1, got {days}")
    return last - dt.timedelta(days=days - 1), last


def streamlit_command(port: int) -> list[str]:
    return [sys.executable, "-m", "streamlit", "run", str(APP), "--server.port", str(port)]


def serve(port: int = DEFAULT_PORT, report: Report = print) -> int:
    """Start the explorer and block until it is stopped with Ctrl+C."""
    if not APP.is_file():
        report(f"{APP} not found. Run this from the repository root.")
        return 1
    report(f"Explorer at http://localhost:{port}  (Ctrl+C to stop)")
    try:
        return subprocess.run(streamlit_command(port), check=False).returncode
    except KeyboardInterrupt:
        return 0


def credentials_help(env_file: Path, created: bool) -> str:
    made = f"Created {env_file} from .env.example. " if created else ""
    return (
        "Space-Track credentials are missing, and the element data cannot be fetched "
        "without them.\n"
        "  1. Create a free account: https://www.space-track.org/auth/createAccount\n"
        f"  2. {made}Put your login in {env_file}:\n"
        "       SPACETRACK_IDENTITY=you@example.org\n"
        "       SPACETRACK_PASSWORD=...\n"
        "  3. Run this command again."
    )


def ensure_env_file(env_file: Path = Path(".env")) -> bool:
    """Copy .env.example to .env if there is no .env yet. True if it copied."""
    example = Path(".env.example")
    if env_file.exists() or not example.exists():
        return False
    shutil.copyfile(example, env_file)
    return True


def demo(days: int = DEFAULT_DAYS, port: int = DEFAULT_PORT, report: Report = print) -> int:
    """Ingest the most recent ``days``, model them, and open the explorer."""
    if not get_settings().spacetrack.is_configured:
        report(credentials_help(Path(".env"), ensure_env_file()))
        return 1

    code = prepare(report)
    if code != 0:
        return code

    start, end = window(dt.date.fromisoformat(backfill.last_partition_key()), days)
    report(f"Landing and modelling {start} to {end}. This takes a few minutes.")
    code = backfill.run(start.isoformat(), end.isoformat(), report=report)
    if code != 0:
        return code

    return serve(port, report)
