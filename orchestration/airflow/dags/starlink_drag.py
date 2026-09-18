"""Airflow DAG for the Starlink Differential Drag Atlas.

This is the same pipeline the Dagster definitions run. Neither orchestrator owns
any logic: the work lives in `starlink_drag.cli.*` and in dbt, and both schedulers
call the same entrypoints. That is deliberate -- an orchestrator is a scheduling
and observability concern, not a place to put business logic, and keeping it that
way is what makes running both cost almost nothing.

Airflow's daily schedule maps onto the same grain as Dagster's
`DailyPartitionsDefinition`: one run per UTC day, fetching that day's GP history.
`catchup=True` means a backfill is expressed by Airflow's own scheduling rather
than by a bespoke loop.

Run it with:  docker compose -f docker-compose.airflow.yml up -d
"""

from __future__ import annotations

import pendulum
from airflow.decorators import dag, task
from airflow.operators.bash import BashOperator

PROJECT = "/opt/airflow/project"

# Matches the v2-overlap analysis window: the period during which all three
# V2 Mini variants were simultaneously on orbit.
START_DATE = pendulum.datetime(2024, 11, 25, tz="UTC")

DEFAULT_ARGS = {
    "owner": "starlink-drag",
    "retries": 3,
    # Space-Track rate limits mean a partition can legitimately need to wait, and
    # a transient 5xx or an expired session should retry rather than fail the day.
    "retry_delay": pendulum.duration(minutes=1),
    "retry_exponential_backoff": True,
    "max_retry_delay": pendulum.duration(minutes=30),
}


@dag(
    dag_id="starlink_drag_daily",
    description="Load Starlink orbital history and space weather, then rebuild dbt models",
    start_date=START_DATE,
    schedule="0 6 * * *",  # Space-Track publishes the previous day overnight
    catchup=True,
    # The rate limiter is per-process, so concurrent runs would breach the API
    # ceiling between them. One run at a time is a correctness constraint here,
    # not a performance preference.
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["starlink", "space-weather", "elt"],
    doc_md=__doc__,
)
def starlink_drag_daily():

    @task(task_id="refresh_satellite_catalogue")
    def refresh_catalogue() -> dict:
        """Rebuild the NORAD ID to hardware generation map from GCAT.

        Fails the run if validation fails: a wrong generation label silently
        corrupts every downstream result while still producing plausible output.
        """
        import sys

        sys.path.insert(0, f"{PROJECT}/src")
        from starlink_drag import config, generation_map

        path = generation_map.fetch_gcat(
            config.DATA_ROOT / "raw" / "gcat_satcat.tsv", refresh=True
        )
        mapping = generation_map.build(generation_map.load_gcat(path))

        failures = generation_map.validate(mapping)
        if failures:
            raise ValueError("generation map validation failed:\n" + "\n".join(failures))

        output = config.DATA_ROOT / "interim" / "starlink_generation_map.parquet"
        output.parent.mkdir(parents=True, exist_ok=True)
        mapping.to_parquet(output, index=False)

        report = generation_map.coverage(mapping)
        return {"satellites": len(mapping), "labelled_pct": round(100 * report.labelled_fraction, 2)}

    @task(task_id="load_space_weather")
    def load_space_weather() -> dict:
        """Pull NASA OMNI indices. Cheap and unpartitioned; skips complete years."""
        import datetime as dt
        import sys

        sys.path.insert(0, f"{PROJECT}/src")
        from starlink_drag import config
        from starlink_drag.ingest import omni

        return omni.backfill(
            dt.date.fromisoformat(config.HISTORY_START), dt.date.today()
        )

    @task(task_id="load_gp_history")
    def load_gp_history(data_interval_start=None, data_interval_end=None) -> dict:
        """Load one UTC day of Space-Track GP history for the whole constellation.

        Idempotent: a day already marked `_SUCCESS` is skipped, so a re-run or a
        cleared task instance costs nothing and cannot corrupt the partition.
        """
        import sys

        sys.path.insert(0, f"{PROJECT}/src")
        from starlink_drag.ingest import spacetrack

        return spacetrack.backfill(
            data_interval_start.date(), data_interval_end.date()
        )

    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=(
            f"cd {PROJECT}/dbt && "
            "DBT_PROFILES_DIR=. dbt build --target dev"
        ),
        doc_md="Builds and tests every model. Fails the run if any data test fails.",
    )

    # The catalogue and space weather are independent of each other; both must
    # land before dbt can resolve dim_satellite and the weather join.
    [refresh_catalogue(), load_space_weather(), load_gp_history()] >> dbt_build


starlink_drag_daily()
