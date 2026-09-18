"""Dagster asset graph for the Starlink Differential Drag Atlas.

Assets are modelled on the *data* rather than on tasks, so the lineage Dagster
draws is the real dependency structure: bronze sources feed a generation
dimension and a set of dbt models that feed the analysis marts.

The GP history asset is daily-partitioned. That is the whole point of using an
orchestrator here rather than a shell script: a 2,500-day backfill against a
rate-limited API needs per-day retry, per-day status, and the ability to resume
after a failure without re-fetching what already landed. Dagster's partition
model gives that directly, and it matches the loader's own _SUCCESS markers.
"""

import datetime as dt

import pandas as pd
from dagster import (
    AssetExecutionContext,
    AssetKey,
    BackfillPolicy,
    DailyPartitionsDefinition,
    MetadataValue,
    Output,
    RetryPolicy,
    asset,
)

from .. import config, generation_map
from ..ingest import omni, spacetrack

# Starlink orbital history begins with the v0.9 batch; nothing before that exists.
GP_PARTITIONS = DailyPartitionsDefinition(start_date=config.HISTORY_START)


@asset(
    group_name="bronze",
    compute_kind="http",
    description="Raw GCAT satellite catalogue: the only public source carrying a "
                "per-satellite spacecraft-bus field alongside mass and span.",
)
def gcat_satcat(context: AssetExecutionContext) -> Output[str]:
    path = generation_map.fetch_gcat(config.DATA_ROOT / "raw" / "gcat_satcat.tsv", refresh=True)
    size_mb = path.stat().st_size / 1e6
    return Output(
        str(path),
        metadata={"path": str(path), "size_mb": round(size_mb, 1)},
    )


@asset(
    group_name="bronze",
    compute_kind="python",
    deps=[gcat_satcat],
    description="NORAD ID -> Starlink hardware generation. Gen2 buses are stated "
                "by GCAT; Gen1 is inferred from launch-mass banding and flagged.",
)
def satellite_generation_map(context: AssetExecutionContext) -> Output[pd.DataFrame]:
    frame = generation_map.load_gcat(config.DATA_ROOT / "raw" / "gcat_satcat.tsv")
    mapping = generation_map.build(frame)

    failures = generation_map.validate(mapping)
    if failures:
        # Fail loudly: a wrong generation label silently corrupts every downstream
        # result while still producing plausible-looking output.
        raise ValueError("generation map validation failed:\n" + "\n".join(failures))

    output = config.DATA_ROOT / "interim" / "starlink_generation_map.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    mapping.to_parquet(output, index=False)

    report = generation_map.coverage(mapping)
    return Output(
        mapping,
        metadata={
            "satellites": len(mapping),
            "labelled_pct": round(100 * report.labelled_fraction, 2),
            "generations": MetadataValue.md(report.per_generation.to_markdown()),
        },
    )


@asset(
    group_name="bronze",
    compute_kind="http",
    description="NASA OMNI hourly space weather: F10.7, Kp, ap, Dst and solar wind.",
)
def omni_space_weather(context: AssetExecutionContext) -> Output[dict]:
    summary = omni.backfill(
        dt.date.fromisoformat(config.HISTORY_START),
        dt.date.today(),
    )
    return Output(summary, metadata={k: v for k, v in summary.items()})


@asset(
    group_name="bronze",
    compute_kind="http",
    partitions_def=GP_PARTITIONS,
    # Space-Track rate limits mean a partition can legitimately need to wait; a
    # transient 500 or an expired session should retry rather than fail the day.
    retry_policy=RetryPolicy(max_retries=3, delay=60),
    # Serial backfill: the rate limiter is per-process, so running partitions
    # concurrently would breach the API ceiling.
    backfill_policy=BackfillPolicy.single_run(),
    description="One UTC day of Space-Track GP history for the whole constellation. "
                "Idempotent: a day already marked _SUCCESS is skipped.",
)
def gp_history(context: AssetExecutionContext) -> Output[dict]:
    window = context.partition_time_window
    start = window.start.date()
    end = window.end.date()

    summary = spacetrack.backfill(start, end)
    context.log.info("loaded %s..%s: %s", start, end, summary)
    return Output(summary, metadata={k: v for k, v in summary.items()})
