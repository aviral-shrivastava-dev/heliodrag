"""Ingestion assets. Wiring only -- the fetching lives in ``starlink_drag.ingest``.

Each asset reads its partition time window and hands it to the same function the
CLI calls, so Dagster and cron drive identical code.
"""

# NOTE: no `from __future__ import annotations` in this module. Dagster
# resolves the `context` parameter's type hint at definition time and rejects a
# stringified one, so asset modules keep runtime annotations.
import datetime as dt

import dagster as dg
from dagster import AssetExecutionContext

from starlink_drag.defs.partitions import daily_partitions
from starlink_drag.defs.resources import AtlasSettings

INGEST_GROUP = "bronze"

SPACETRACK_CONCURRENCY = {"dagster/concurrency_key": "spacetrack"}
"""Space-Track assets share one concurrency slot.

The rate limiter keeps its window in process memory (ADR-0003), so two assets
fetching at once would each believe they were within the limit while together
exceeding it. This tag is the enforcement; removing it re-opens that hole.
"""

INGEST_RETRY = dg.RetryPolicy(
    max_retries=3,
    delay=30,
    backoff=dg.Backoff.EXPONENTIAL,
    jitter=dg.Jitter.PLUS_MINUS,
)
"""Retry ingestion with exponential backoff and jitter.

This is the *run-level* retry, above the per-request retry inside the client.
The client handles a single flaky response; this handles a whole partition
failing -- an expired session, a network drop mid-backfill, a Space-Track
outage. Jitter for the same reason as in the client: identical delays would send
several failed partitions back in lockstep.
"""


def _window(context: AssetExecutionContext) -> tuple[dt.date, dt.date]:
    """The half-open date range this run covers.

    With ``BackfillPolicy.single_run`` this spans the whole backfill, not one
    day, which is what lets a six-year backfill be a few hundred requests rather
    than tens of thousands.
    """
    window = context.partition_time_window
    return window.start.date(), window.end.date()


@dg.asset(
    group_name=INGEST_GROUP,
    retry_policy=INGEST_RETRY,
    op_tags=SPACETRACK_CONCURRENCY,
    description=(
        "Space-Track catalogue snapshot. Unpartitioned by time: it is the "
        "current state of the catalogue, not a time series, so each run records "
        "one dated snapshot and comparing two is how a decay is detected."
    ),
)
def bronze_satcat(context: AssetExecutionContext, settings: AtlasSettings) -> dg.MaterializeResult:
    from starlink_drag.ingest.satcat import ingest_satcat

    outcome = ingest_satcat(settings.load())
    context.log.info(outcome.describe())
    return dg.MaterializeResult(
        metadata={
            "rows": outcome.rows_written,
            "quarantined": outcome.rows_quarantined,
            "snapshot_date": outcome.partitions[0] if outcome.partitions else "none",
        }
    )


@dg.asset(
    group_name=INGEST_GROUP,
    partitions_def=daily_partitions,
    backfill_policy=dg.BackfillPolicy.single_run(),
    retry_policy=INGEST_RETRY,
    description=(
        "NASA OMNI hourly space weather. Public and unrated, so the whole "
        "requested range is fetched in one request and split into daily "
        "partitions on write."
    ),
)
def bronze_omni(context: AssetExecutionContext, settings: AtlasSettings) -> dg.MaterializeResult:
    from starlink_drag.ingest.omni import ingest_omni

    start, end = _window(context)
    outcome = ingest_omni(settings.load(), start, end)
    context.log.info(outcome.describe())
    return dg.MaterializeResult(
        metadata={
            "rows": outcome.rows_written,
            "partitions": len(outcome.partitions),
            "quarantined": outcome.rows_quarantined,
            "window": f"{start} .. {end}",
        }
    )


@dg.asset(
    group_name=INGEST_GROUP,
    partitions_def=daily_partitions,
    backfill_policy=dg.BackfillPolicy.single_run(),
    retry_policy=INGEST_RETRY,
    op_tags=SPACETRACK_CONCURRENCY,
    deps=[bronze_satcat],
    description=(
        "Space-Track general-perturbations elements. Depends on the catalogue "
        "because the object list comes from it -- fetching elements for a "
        "satellite that had not launched wastes a request against a hard limit."
    ),
)
def bronze_gp_history(
    context: AssetExecutionContext, settings: AtlasSettings
) -> dg.MaterializeResult:
    from starlink_drag.ingest.gp import ingest_gp
    from starlink_drag.ingest.satcat import norad_ids

    resolved = settings.load()
    start, end = _window(context)

    objects = norad_ids(resolved, on_orbit_during=(start, end))
    context.log.info(f"{len(objects):,} objects on orbit during {start}..{end}")

    report = ingest_gp(
        resolved,
        start,
        end,
        objects,
        on_progress=context.log.info,
    )
    context.log.info(report.describe())

    if report.chunks_failed:
        raise RuntimeError(
            f"{report.chunks_failed} of {report.requests} chunks failed; "
            "re-run this partition range to fill the gaps"
        )

    return dg.MaterializeResult(
        metadata={
            "rows": report.rows_written,
            "partitions": report.partitions,
            "requests": report.requests,
            "quarantined": report.rows_quarantined,
            "objects": len(objects),
            "window": f"{start} .. {end}",
        }
    )


ingest_assets = [bronze_satcat, bronze_omni, bronze_gp_history]
