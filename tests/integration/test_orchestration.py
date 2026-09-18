"""The Dagster layer: graph shape, partition windows, and retry behaviour.

No network. The ingestion functions are substituted; what is under test is the
wiring, which is all ``defs/`` is allowed to contain.
"""

# NOTE: no `from __future__ import annotations` -- Dagster rejects a
# stringified `context` hint, same reason as in src/starlink_drag/defs.

import datetime as dt
from typing import Any

import dagster as dg
import pytest
from dagster import AssetExecutionContext

from starlink_drag.defs.ingest.assets import (
    INGEST_RETRY,
    SPACETRACK_CONCURRENCY,
    bronze_gp_history,
    bronze_omni,
    bronze_satcat,
)
from starlink_drag.defs.partitions import daily_partitions
from starlink_drag.defs.transform.assets import warehouse_asset_key

pytestmark = pytest.mark.integration


# -- the graph -------------------------------------------------------------


def test_the_graph_runs_ingestion_then_views_then_dbt() -> None:
    """A reviewer should be able to read the lineage in one direction."""
    from starlink_drag.definitions import defs

    graph = defs.get_repository_def().asset_graph

    def parents(name: str) -> set[str]:
        return {k.to_user_string() for k in graph.get(dg.AssetKey(name)).parent_keys}

    assert parents("bronze_gp_history") == {"bronze_satcat"}
    assert parents("warehouse_gp_history") == {
        "bronze_satcat",
        "bronze_omni",
        "bronze_gp_history",
    }
    assert parents("stg_spacetrack__gp_history") == {"warehouse_gp_history"}
    assert parents("int_gp__deduplicated") == {"stg_spacetrack__gp_history"}
    assert "int_decay__daily_rates" in parents("fct_daily_decay")


def test_dbt_models_are_surfaced_as_assets() -> None:
    from starlink_drag.definitions import defs

    keys = {k.to_user_string() for k in defs.get_repository_def().asset_graph.get_all_asset_keys()}

    for model in (
        "stg_spacetrack__gp_history",
        "int_gp__deduplicated",
        "int_satellite__generation_labeled",
        "int_decay__daily_rates",
        "dim_satellite",
        "dim_generation",
        "fct_daily_decay",
        "fct_storm_epoch",
    ):
        assert model in keys, model


def test_marts_and_staging_are_grouped_by_medallion_tag() -> None:
    from starlink_drag.definitions import defs

    graph = defs.get_repository_def().asset_graph

    assert graph.get(dg.AssetKey("fct_daily_decay")).group_name == "gold"
    assert graph.get(dg.AssetKey("stg_nasa__omni")).group_name == "silver"
    assert graph.get(dg.AssetKey("bronze_gp_history")).group_name == "bronze"


def test_the_custom_checks_are_registered() -> None:
    from starlink_drag.definitions import defs

    names = {c.name for c in defs.get_repository_def().asset_graph.asset_check_keys}

    assert {
        "gp_history_is_fresh",
        "omni_is_fresh",
        "fct_daily_decay_has_volume",
        "fct_daily_decay_null_rate",
    } <= names


def test_warehouse_views_cover_every_dbt_source() -> None:
    from starlink_drag.definitions import defs
    from starlink_drag.warehouse import VIEWS

    keys = {k.to_user_string() for k in defs.get_repository_def().asset_graph.get_all_asset_keys()}

    for view in VIEWS.values():
        assert warehouse_asset_key(view).to_user_string() in keys


# -- partitioning ----------------------------------------------------------


def test_ingestion_is_daily_partitioned() -> None:
    assert bronze_omni.partitions_def == daily_partitions
    assert bronze_gp_history.partitions_def == daily_partitions
    # The catalogue is a snapshot of current state, not a time series.
    assert bronze_satcat.partitions_def is None


def test_a_backfill_is_one_run_over_the_whole_range() -> None:
    """The rate limit makes a run per day impossible: six years is ~80,000
    requests, which at under 300 an hour is a week and a half."""
    for asset in (bronze_omni, bronze_gp_history):
        policy = asset.backfill_policy
        assert policy is not None
        assert policy.max_partitions_per_run is None, "expected single_run"


def test_partitions_start_at_the_configured_pipeline_start() -> None:
    assert daily_partitions.start.date() == dt.date(2020, 1, 1)


# -- retries ---------------------------------------------------------------


def test_ingestion_retries_with_exponential_backoff_and_jitter() -> None:
    """Above the client's per-request retry: this is for a whole partition
    failing on an expired session or a network drop mid-backfill."""
    assert INGEST_RETRY.max_retries == 3
    assert INGEST_RETRY.backoff == dg.Backoff.EXPONENTIAL
    assert INGEST_RETRY.jitter == dg.Jitter.PLUS_MINUS

    for asset in (bronze_satcat, bronze_omni, bronze_gp_history):
        policy = asset.op.retry_policy
        assert policy is not None, asset.key
        assert policy.max_retries == 3, asset.key


def test_space_track_assets_share_one_concurrency_slot() -> None:
    """The rate limiter's window is per-process (ADR-0003), so two Space-Track
    assets running at once would together exceed a limit each believed it was
    within."""
    assert SPACETRACK_CONCURRENCY == {"dagster/concurrency_key": "spacetrack"}

    for asset in (bronze_satcat, bronze_gp_history):
        tags = asset.op.tags
        assert tags.get("dagster/concurrency_key") == "spacetrack", asset.key

    # OMNI is public and unrated, so it is deliberately not constrained.
    assert "dagster/concurrency_key" not in bronze_omni.op.tags


def test_a_failing_partition_retries_and_then_recovers() -> None:
    """The acceptance criterion, exercised end to end in-process.

    An asset that fails its first two attempts and succeeds on the third must
    materialise, not fail the run.
    """
    attempts: list[int] = []

    @dg.asset(
        name="flaky_partition",
        partitions_def=daily_partitions,
        retry_policy=dg.RetryPolicy(max_retries=3, delay=0),
    )
    def flaky(context: AssetExecutionContext) -> dg.MaterializeResult:
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise RuntimeError(f"attempt {len(attempts)} fails on purpose")
        return dg.MaterializeResult(metadata={"attempts": len(attempts)})

    result = dg.materialize([flaky], partition_key="2024-05-10", raise_on_error=False)

    assert result.success, "the run should recover, not fail"
    assert len(attempts) == 3, f"expected two failures then a success, got {attempts}"


def test_retries_are_bounded_and_a_hopeless_partition_fails() -> None:
    """A partition that never recovers must fail rather than retry forever."""
    attempts: list[int] = []

    @dg.asset(
        name="always_broken",
        partitions_def=daily_partitions,
        retry_policy=dg.RetryPolicy(max_retries=2, delay=0),
    )
    def broken(context: AssetExecutionContext) -> None:
        attempts.append(1)
        raise RuntimeError("this one never works")

    result = dg.materialize([broken], partition_key="2024-05-10", raise_on_error=False)

    assert not result.success
    assert len(attempts) == 3, "one attempt plus two retries"


# -- the window handed to the fetchers -------------------------------------


def test_the_asset_hands_its_whole_window_to_the_fetcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A single-run backfill must fetch the range, not repeat one day."""
    from starlink_drag.config import Settings
    from starlink_drag.defs import resources as resources_module
    from starlink_drag.ingest import omni as omni_module

    seen: dict[str, dt.date] = {}

    def fake_ingest(settings: Any, start: dt.date, end: dt.date, **kwargs: Any) -> Any:
        from starlink_drag.ingest.bronze import LoadOutcome

        seen["start"], seen["end"] = start, end
        return LoadOutcome("bronze_omni", ("2024-05-10",), 24, 0)

    monkeypatch.setattr(omni_module, "ingest_omni", fake_ingest)
    monkeypatch.setattr(
        resources_module.AtlasSettings, "load", lambda self: Settings(data_dir=tmp_path)
    )

    result = dg.materialize(
        [bronze_omni],
        partition_key="2024-05-10",
        resources={"settings": resources_module.AtlasSettings()},
    )

    assert result.success
    assert seen["start"] == dt.date(2024, 5, 10)
    assert seen["end"] == dt.date(2024, 5, 11)
