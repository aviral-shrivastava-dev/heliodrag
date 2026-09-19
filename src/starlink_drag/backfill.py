"""Drive a full backfill through Dagster.

One command's worth of orchestration, kept out of ``cli.py`` because it is
sequencing logic rather than argument parsing.

It issues **three** Dagster runs rather than one. ``--partition-range`` refuses
a selection containing anything unpartitioned, and both the catalogue snapshot
and everything downstream of bronze are unpartitioned by design, so the range
can only be applied to the two partitioned sources.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

DAGSTER_MODULE = "starlink_drag.definitions"


@dataclass(frozen=True, slots=True)
class Step:
    """One Dagster invocation in the sequence."""

    label: str
    selection: str
    partitioned: bool


STEPS: tuple[Step, ...] = (
    Step("catalogue", "bronze_satcat", partitioned=False),
    Step("sources", "bronze_omni,bronze_gp_history", partitioned=True),
    Step("views and models", "group:warehouse,group:silver,group:gold", partitioned=False),
)
"""In order. The catalogue first, because the element fetch takes its object
list from it; the views and models last, because they read what the first two
produced."""


def last_partition_key() -> str:
    """The newest partition Dagster actually has.

    Asked for rather than computed. Partitions are UTC and a daily partition
    exists only once its day is complete, so "yesterday" in local time can be
    two days past the last real partition -- which fails the run with an
    unhelpful "Could not find a partition with key ...". That is not
    hypothetical; it is how the first full-backfill attempt failed.
    """
    from starlink_drag.defs.partitions import daily_partitions

    key = daily_partitions.get_last_partition_key()
    if key is None:
        raise RuntimeError("no partitions exist yet; check PIPELINE_START_DATE")
    return str(key)


def materialize(selection: str, partition_range: str | None = None) -> int:
    """Invoke ``dagster asset materialize`` for one selection.

    The selection is never ``*``: a bare asterisk is expanded by the shell into
    the working directory's file list before Dagster ever sees it, and Dagster
    then complains about being handed ``README.md``.
    """
    command = [
        sys.executable,
        "-m",
        "dagster",
        "asset",
        "materialize",
        "-m",
        DAGSTER_MODULE,
        "--select",
        selection,
    ]
    if partition_range:
        command += ["--partition-range", partition_range]

    return subprocess.run(command, check=False).returncode


def run(
    start: str,
    end: str | None = None,
    *,
    report: Callable[[str], None] = print,
) -> int:
    """Run every step in order. Returns the exit code of the first failure."""
    last = end or last_partition_key()
    partition_range = f"{start}...{last}"

    for step in STEPS:
        span = partition_range if step.partitioned else None
        report("")
        report(f"[{step.label}] {step.selection}" + (f" over {span}" if span else ""))

        code = materialize(step.selection, span)
        if code != 0:
            report(f"[{step.label}] failed with exit code {code}")
            return code

    report("")
    report(f"backfill complete: {partition_range}")
    return 0
