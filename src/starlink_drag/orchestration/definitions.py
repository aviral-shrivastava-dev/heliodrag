"""Dagster entrypoint: assets, dbt models, schedules and sensors.

    dagster dev -m starlink_drag.orchestration.definitions

The dbt project is loaded as Dagster assets so lineage runs unbroken from the
HTTP pull through to the analysis marts, rather than showing dbt as one opaque
box at the end.
"""

import pathlib
import sys

from dagster import (
    AssetExecutionContext,
    AssetSelection,
    DefaultScheduleStatus,
    Definitions,
    ScheduleDefinition,
    define_asset_job,
)
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

from .. import config
from . import assets as ingest_assets

DBT_DIR = config.PROJECT_ROOT / "dbt"


def _dbt_executable() -> str:
    """Locate the dbt CLI beside the current interpreter, falling back to PATH."""
    scripts = pathlib.Path(sys.executable).parent
    for candidate in ("dbt.exe", "dbt"):
        path = scripts / candidate
        if path.exists():
            return str(path)
    return "dbt"


dbt_project = DbtProject(
    project_dir=DBT_DIR,
    profiles_dir=DBT_DIR,
)
dbt_project.prepare_if_dev()


@dbt_assets(manifest=dbt_project.manifest_path)
def dbt_models(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()


# Space weather and the satellite catalogue are cheap and change daily; the
# orbital history is the expensive, rate-limited one and is backfilled separately
# by partition rather than swept up in the daily refresh.
daily_refresh = define_asset_job(
    name="daily_refresh",
    selection=(
        AssetSelection.assets(
            ingest_assets.gcat_satcat,
            ingest_assets.satellite_generation_map,
            ingest_assets.omni_space_weather,
        )
        | AssetSelection.assets(dbt_models)
    ),
    description="Refresh catalogue and space weather, then rebuild all dbt models.",
)

gp_backfill = define_asset_job(
    name="gp_history_backfill",
    selection=AssetSelection.assets(ingest_assets.gp_history),
    description="Load Space-Track GP history one day-partition at a time.",
)

defs = Definitions(
    assets=[
        ingest_assets.gcat_satcat,
        ingest_assets.satellite_generation_map,
        ingest_assets.omni_space_weather,
        ingest_assets.gp_history,
        dbt_models,
    ],
    jobs=[daily_refresh, gp_backfill],
    schedules=[
        ScheduleDefinition(
            job=daily_refresh,
            # Space-Track publishes the previous day's elements overnight; 06:00 UTC
            # is comfortably after that without competing with the busiest window.
            cron_schedule="0 6 * * *",
            execution_timezone="UTC",
            default_status=DefaultScheduleStatus.STOPPED,
        ),
    ],
    resources={
        "dbt": DbtCliResource(
            project_dir=dbt_project,
            profiles_dir=str(DBT_DIR),
            # Resolve dbt next to the running interpreter rather than trusting PATH,
            # so this works from a venv without the venv being activated.
            dbt_executable=_dbt_executable(),
        ),
    },
)
