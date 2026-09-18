"""Dagster entry point.

``dagster dev -m starlink_drag.definitions`` loads this. It stays thin: assets,
checks and resources are defined under ``starlink_drag.defs`` and only assembled
here.

The graph reads in one direction:

    bronze_satcat ─┐
    bronze_omni   ─┼─> warehouse_* views ─> staging ─> intermediate ─> marts
    bronze_gp_history ─┘

Ingestion assets are daily-partitioned with ``BackfillPolicy.single_run``, so a
six-year backfill is one run issuing a few hundred wide requests rather than one
run per day issuing tens of thousands. dbt models are unpartitioned: they rebuild
from whatever bronze currently holds.
"""

# NOTE: no `from __future__ import annotations` here -- see defs/ingest/assets.py.

import dagster as dg

from starlink_drag.defs.checks.assets import checks
from starlink_drag.defs.dbt.assets import dbt_models
from starlink_drag.defs.ingest.assets import bronze_gp_history, bronze_omni, bronze_satcat
from starlink_drag.defs.partitions import daily_partitions
from starlink_drag.defs.resources import resources
from starlink_drag.defs.transform.assets import bronze_views

all_assets = [bronze_satcat, bronze_omni, bronze_gp_history, bronze_views, dbt_models]

ingest_job = dg.define_asset_job(
    name="ingest",
    selection=dg.AssetSelection.assets(bronze_satcat, bronze_omni, bronze_gp_history),
    partitions_def=daily_partitions,
    description="Land the raw sources for a partition range.",
)

atlas_job = dg.define_asset_job(
    name="atlas",
    selection=dg.AssetSelection.all(),
    partitions_def=daily_partitions,
    description=(
        "Everything: ingest a partition range, rebuild the bronze views, then "
        "run dbt build. This is what the daily schedule and the backfill use."
    ),
)

daily_schedule = dg.build_schedule_from_partitioned_job(
    atlas_job,
    hour_of_day=6,
    description=(
        "Run yesterday's partition each morning. Space-Track publishes on a lag, "
        "so an early-hours run would chase data that has not been posted."
    ),
)

defs = dg.Definitions(
    assets=all_assets,
    asset_checks=checks,
    jobs=[ingest_job, atlas_job],
    schedules=[daily_schedule],
    resources=resources(),
)
