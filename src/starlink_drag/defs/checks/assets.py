"""Asset checks: freshness, volume and null rate.

These answer questions a green run cannot. A materialisation says the code ran;
a check says the data that came out is usable. They are deliberately separate,
so a stale-but-successful run is visibly stale rather than quietly fine.

Each check reads the warehouse directly rather than trusting run metadata,
because the question is what is *in* the table, not what the last run claimed.
"""

# NOTE: no `from __future__ import annotations` here -- see defs/ingest/assets.py.

import datetime as dt
from pathlib import Path
from typing import Any

import dagster as dg
import duckdb
from dagster import AssetCheckExecutionContext

from starlink_drag import lake
from starlink_drag.defs.resources import AtlasSettings
from starlink_drag.defs.transform.assets import warehouse_asset_key

GP_STALENESS_LIMIT_DAYS = 3
"""Space-Track publishes roughly 1.24 element sets per satellite per day, so a
gap beyond three days is an outage rather than a quiet spell."""

MINIMUM_DAILY_DECAY_ROWS = 1_000
"""A year of data is millions of rows. A thousand means something is badly
wrong -- an empty partition range, or views pointing at nothing."""

MAXIMUM_NULL_RATE = 0.05
"""Null rate allowed in the columns the analysis cannot work without."""


def _query(settings: AtlasSettings, sql: str) -> Any:
    resolved = settings.load()
    database = Path(resolved.duckdb_path)
    if not database.exists():
        return None
    with duckdb.connect(str(database), read_only=True) as con:
        # The bronze views hold s3:// paths when the lake is remote; reading
        # them needs the lake's keys in this connection too.
        secret = lake.duckdb_secret(resolved)
        if secret:
            con.execute(secret)
        return con.execute(sql).fetchone()


def _missing(table: str) -> dg.AssetCheckResult:
    return dg.AssetCheckResult(
        passed=False,
        severity=dg.AssetCheckSeverity.WARN,
        description=f"{table} is not in the warehouse yet; nothing to check.",
    )


# -- freshness -------------------------------------------------------------


@dg.asset_check(
    asset=warehouse_asset_key("gp_history"),
    name="gp_history_is_fresh",
    description=(
        "The newest epoch in bronze is within a few days of today. Catches a "
        "silently stalled ingest, which a successful run cannot."
    ),
    blocking=False,
)
def gp_history_is_fresh(
    context: AssetCheckExecutionContext, settings: AtlasSettings
) -> dg.AssetCheckResult:
    row = _query(settings, "select max(epoch_date) from bronze.gp_history")
    if row is None:
        return _missing("bronze.gp_history")

    latest = row[0]
    if latest is None:
        return _missing("bronze.gp_history")

    age = (dt.date.today() - latest).days
    context.log.info(f"newest epoch {latest}, {age} days old")

    return dg.AssetCheckResult(
        passed=age <= GP_STALENESS_LIMIT_DAYS,
        severity=dg.AssetCheckSeverity.WARN,
        metadata={"latest_epoch_date": str(latest), "age_days": age},
        description=(f"Newest epoch is {age} days old (limit {GP_STALENESS_LIMIT_DAYS})."),
    )


@dg.asset_check(
    asset=warehouse_asset_key("omni"),
    name="omni_is_fresh",
    description="Space weather keeps up with the elements it will be joined to.",
    blocking=False,
)
def omni_is_fresh(
    context: AssetCheckExecutionContext, settings: AtlasSettings
) -> dg.AssetCheckResult:
    row = _query(settings, "select max(epoch_date) from bronze.omni")
    if row is None or row[0] is None:
        return _missing("bronze.omni")

    age = (dt.date.today() - row[0]).days
    return dg.AssetCheckResult(
        passed=age <= GP_STALENESS_LIMIT_DAYS,
        severity=dg.AssetCheckSeverity.WARN,
        metadata={"latest_epoch_date": str(row[0]), "age_days": age},
        description=f"Newest OMNI day is {age} days old.",
    )


# -- volume ----------------------------------------------------------------


@dg.asset_check(
    asset=dg.AssetKey("fct_daily_decay"),
    name="fct_daily_decay_has_volume",
    description="The mart is populated, and most of it is analysis-ready.",
    blocking=False,
)
def fct_daily_decay_has_volume(
    context: AssetCheckExecutionContext, settings: AtlasSettings
) -> dg.AssetCheckResult:
    row = _query(
        settings,
        "select count(*), count(*) filter (where is_analysis_ready) from main.fct_daily_decay",
    )
    if row is None:
        return _missing("fct_daily_decay")

    total, ready = int(row[0]), int(row[1])
    ready_fraction = ready / total if total else 0.0
    context.log.info(f"{total:,} rows, {ready:,} analysis-ready")

    return dg.AssetCheckResult(
        passed=total >= MINIMUM_DAILY_DECAY_ROWS and ready_fraction >= 0.5,
        metadata={
            "rows": total,
            "analysis_ready": ready,
            "analysis_ready_pct": round(100 * ready_fraction, 1),
        },
        description=(
            f"{total:,} rows, {ready_fraction:.0%} analysis-ready "
            f"(need {MINIMUM_DAILY_DECAY_ROWS:,} rows and 50%)."
        ),
    )


# -- null rate -------------------------------------------------------------


@dg.asset_check(
    asset=dg.AssetKey("fct_daily_decay"),
    name="fct_daily_decay_null_rate",
    description=(
        "Columns the analysis cannot work without are populated. Space weather "
        "is checked separately because a missing OMNI day is an upstream gap, "
        "not a broken model."
    ),
    blocking=False,
)
def fct_daily_decay_null_rate(
    context: AssetCheckExecutionContext, settings: AtlasSettings
) -> dg.AssetCheckResult:
    row = _query(
        settings,
        """
        select
            count(*),
            count(*) filter (where mean_altitude_km is null),
            count(*) filter (where generation is null),
            count(*) filter (where dst_min_nt is null),
            count(*) filter (where altitude_rate_km_per_day is null and is_analysis_ready)
        from main.fct_daily_decay
        """,
    )
    if row is None:
        return _missing("fct_daily_decay")

    total = int(row[0])
    if total == 0:
        return _missing("fct_daily_decay")

    rates = {
        "mean_altitude_km": int(row[1]) / total,
        "generation": int(row[2]) / total,
        "dst_min_nt": int(row[3]) / total,
    }
    ready_without_rate = int(row[4])

    breached = {k: v for k, v in rates.items() if v > MAXIMUM_NULL_RATE}
    context.log.info(f"null rates: { {k: round(v, 4) for k, v in rates.items()} }")

    return dg.AssetCheckResult(
        passed=not breached and ready_without_rate == 0,
        metadata={
            **{f"null_pct/{k}": round(100 * v, 2) for k, v in rates.items()},
            "analysis_ready_without_a_rate": ready_without_rate,
        },
        description=(
            "all within tolerance"
            if not breached
            else f"over {MAXIMUM_NULL_RATE:.0%}: {', '.join(breached)}"
        ),
    )


checks = [
    gp_history_is_fresh,
    omni_is_fresh,
    fct_daily_decay_has_volume,
    fct_daily_decay_null_rate,
]
