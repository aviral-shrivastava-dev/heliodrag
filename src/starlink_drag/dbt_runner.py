"""Run dbt as a subprocess, the same way from every caller.

dbt is run from the environment this process is running in -- the ``dbt``
executable installed beside this interpreter -- rather than whatever ``dbt`` is
first on PATH, which on a fresh clone depends on the platform and the shell.
``python -m dbt.cli.main`` would also avoid PATH, but prints a RuntimeWarning
about "unpredictable behaviour" that a newcomer should not have to wonder about.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

TRANSFORM = Path("transform")


def _dbt_executable() -> list[str]:
    beside = shutil.which("dbt", path=str(Path(sys.executable).parent))
    return [beside] if beside else [sys.executable, "-m", "dbt.cli.main"]


def dbt(*args: str, database: Path | None = None, target: Path | None = None) -> int:
    """Run one dbt command against the project in ``transform/``."""
    command = [
        *_dbt_executable(),
        *args,
        "--project-dir",
        str(TRANSFORM),
        "--profiles-dir",
        str(TRANSFORM),
    ]
    if target is not None:
        command += ["--target-path", str(target)]
    environment = dict(os.environ)
    if database is not None:
        environment["DUCKDB_PATH"] = str(database)
    return subprocess.run(command, env=environment, check=False).returncode


def prepare(report: Callable[[str], None] = print) -> int:
    """Install the dbt packages if absent, then write the manifest.

    Dagster reads ``transform/target/manifest.json`` when it loads the asset
    graph, and neither the packages nor the manifest are committed. A fresh
    clone therefore needs both before any Dagster command will start.
    """
    if not TRANSFORM.is_dir():
        report("No transform/ directory here. Run this from the repository root.")
        return 1
    if not (TRANSFORM / "dbt_packages").is_dir():
        report("Installing dbt packages")
        code = dbt("deps")
        if code != 0:
            return code
    report("Parsing the dbt project")
    return dbt("parse")
