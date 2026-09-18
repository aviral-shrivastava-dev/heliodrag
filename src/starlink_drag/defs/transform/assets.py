"""The bridge between bronze Iceberg and the warehouse dbt reads. Wiring only."""

# NOTE: no `from __future__ import annotations` in this module. Dagster
# resolves the `context` parameter's type hint at definition time and rejects a
# stringified one, so asset modules keep runtime annotations.

from collections.abc import Iterator

import dagster as dg
from dagster import AssetExecutionContext

from starlink_drag.defs.ingest.assets import bronze_gp_history, bronze_omni, bronze_satcat
from starlink_drag.defs.resources import AtlasSettings
from starlink_drag.warehouse import VIEWS

WAREHOUSE_GROUP = "warehouse"

WAREHOUSE_ASSET_PREFIX = "warehouse"


def warehouse_asset_key(view: str) -> dg.AssetKey:
    """The Dagster key for one bronze view.

    dbt's sources are mapped onto these keys, so the graph reads
    ``bronze_gp_history`` (the Iceberg table) -> ``warehouse_gp_history`` (the
    DuckDB view) -> ``stg_spacetrack__gp_history``. Without that middle node dbt
    and the view-builder would both depend only on ingestion, and Dagster would
    be free to run dbt against stale views.
    """
    return dg.AssetKey(f"{WAREHOUSE_ASSET_PREFIX}_{view}")


@dg.multi_asset(
    group_name=WAREHOUSE_GROUP,
    specs=[
        dg.AssetSpec(
            key=warehouse_asset_key(view),
            deps=[bronze_satcat, bronze_omni, bronze_gp_history],
            description=(
                f"DuckDB view over the current Iceberg snapshot of {table}. "
                "Rebuilt on every run, because the file list changes with every "
                "ingest and DuckDB's iceberg_scan cannot open this lake "
                "(see docs/phases/phase-2.md)."
            ),
        )
        for table, view in VIEWS.items()
    ],
    can_subset=False,
)
def bronze_views(context: AssetExecutionContext, settings: AtlasSettings) -> Iterator[object]:
    """Rebuild every bronze view in one pass.

    One op for all five because ``warehouse.sync`` opens the database once and
    rebuilds them together; splitting it would mean five connections and five
    chances to leave the set half-updated.
    """
    from starlink_drag import warehouse

    results = warehouse.sync(settings.load())
    context.log.info(warehouse.describe(results))

    by_view = {result.view: result for result in results}
    for view in VIEWS.values():
        result = by_view[view]
        yield dg.MaterializeResult(
            asset_key=warehouse_asset_key(view),
            metadata={
                "rows": result.rows,
                "files": result.files,
                "status": "ok" if result.created else (result.skipped_reason or "empty"),
            },
        )
