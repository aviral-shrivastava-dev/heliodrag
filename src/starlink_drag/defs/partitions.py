"""Partition definitions.

One daily partition per `epoch_date`, which is the grain everything downstream
is keyed on. A backfill is expressed as a partition range, so there are no
bespoke backfill scripts.
"""

from __future__ import annotations

import dagster as dg

from starlink_drag.config import get_settings

DAILY_PARTITION_FORMAT = "%Y-%m-%d"


def _start_date() -> str:
    return get_settings().pipeline_start_date.strftime(DAILY_PARTITION_FORMAT)


daily_partitions = dg.DailyPartitionsDefinition(start_date=_start_date())
"""Daily partitions from PIPELINE_START_DATE to today.

Space-Track's rate limit makes a *request* per day impossible -- six years is
roughly eighty thousand requests, which at fewer than 300 an hour is a week and
a half of wall clock. The assets therefore carry
``BackfillPolicy.single_run()``: a backfill of any range executes as ONE run
that reads its whole time window and fetches in wide chunks, while still
recording every day as its own partition.

So the partition grain is the day, and the fetch grain is the window. Those are
deliberately different, and the asset is where they meet.
"""
