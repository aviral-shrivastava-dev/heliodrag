"""dbt models surfaced as Dagster assets. Wiring only."""

# NOTE: no `from __future__ import annotations` in this module. Dagster
# resolves the `context` parameter's type hint at definition time and rejects a
# stringified one, so asset modules keep runtime annotations.
import os
from collections.abc import Iterator, Mapping
from typing import Any

import dagster as dg
from dagster import AssetExecutionContext
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets

from starlink_drag import lake
from starlink_drag.defs.resources import AtlasSettings, dbt_project
from starlink_drag.defs.transform.assets import warehouse_asset_key


class AtlasDbtTranslator(DagsterDbtTranslator):
    """Point each dbt source at the asset that actually produces it.

    A dbt source here is a DuckDB view, and those views are produced by the
    ``bronze_views`` multi-asset. Mapping them onto its keys makes the graph say
    what is true -- Iceberg table, then view, then model -- instead of leaving
    five source nodes dangling with nothing upstream. It also fixes ordering:
    without this edge, dbt and the view-builder would both depend only on
    ingestion, and Dagster would be free to run dbt against stale views.
    """

    def get_asset_key(self, dbt_resource_props: Mapping[str, Any]) -> dg.AssetKey:
        if dbt_resource_props["resource_type"] == "source":
            return warehouse_asset_key(dbt_resource_props["name"])
        return super().get_asset_key(dbt_resource_props)

    def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str | None:
        """Group by medallion tag, so the UI separates silver from gold."""
        tags = dbt_resource_props.get("tags", [])
        for layer in ("gold", "silver"):
            if layer in tags:
                return layer
        inherited: str | None = super().get_group_name(dbt_resource_props)
        return inherited


@dbt_assets(
    manifest=dbt_project.manifest_path,
    dagster_dbt_translator=AtlasDbtTranslator(),
)
def dbt_models(
    context: AssetExecutionContext, dbt: DbtCliResource, settings: AtlasSettings
) -> Iterator[Any]:
    """Run `dbt build`, so models and their tests materialise together.

    `build` rather than `run`: a model that passes its tests is the asset, and
    splitting them would let a failing test sit downstream of a green asset.
    """
    # dagster-dbt runs dbt with the project directory as the working directory,
    # where the Makefile runs it from the repository root. A relative warehouse
    # path therefore resolves to two different files depending on who invoked
    # it, so it is made absolute here -- the one place that knows both.
    resolved = settings.load()
    os.environ["DUCKDB_PATH"] = str(resolved.duckdb_path.resolve())
    # dbt reads the lake's address and keys from real environment variables,
    # not from .env, so they are handed over explicitly (see profiles.yml).
    os.environ.update(lake.dbt_environment(resolved))
    yield from dbt.cli(["build"], context=context).stream()
