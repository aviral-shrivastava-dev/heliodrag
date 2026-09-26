"""Read the gold marts, and nothing else.

Two rules, both enforced rather than intended.

**Gold only.** The explorer reads the tables dbt tags ``gold`` and never a
staging or intermediate model. Silver is free to change shape between phases;
gold is the contract. ``GOLD_MARTS`` names them, one test asserts every query
here reads only those tables, and another asserts the list matches dbt's tag.

**Short-lived, read-only connections.** DuckDB allows one writer, and a reader
that keeps the file open stops the writer from starting. An explorer holding a
connection would make the next ``dbt build`` fail with a lock error. So each
query opens the file read-only, reads, and closes, and the app never holds it
between clicks. Caching the results is the app's job, not this module's.

Rates are converted from km/day to **m/day** here, once, because the typical
operational decay is tens of metres a day and 0.0177 km/day reads as nothing.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import duckdb
import polars as pl

GOLD_MARTS: Final[frozenset[str]] = frozenset(
    {
        "dim_generation",
        "dim_satellite",
        "fct_daily_decay",
        "fct_space_weather_daily",
        "fct_storm_epoch",
    }
)


class WarehouseUnavailable(RuntimeError):
    """The warehouse cannot be read, with a message a person can act on."""


@dataclass(frozen=True, slots=True)
class Coverage:
    """What the warehouse holds, to bound the explorer's controls."""

    first_date: dt.date
    last_date: dt.date
    satellite_days: int
    satellites: int
    generations: tuple[str, ...]
    lowest_shell_km: float
    highest_shell_km: float


@dataclass(frozen=True, slots=True)
class Slice:
    """The part of the data every chart on the page is drawn from.

    One slice for the whole page, so the decay panels, the space-weather strip
    and the storm response always describe the same satellites and dates.
    """

    start: dt.date
    end: dt.date
    generations: tuple[str, ...]
    lowest_shell_km: float
    highest_shell_km: float
    analysis_ready_only: bool = True
    min_satellites: int = 10

    def params(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "generations": list(self.generations),
            "lowest_shell_km": self.lowest_shell_km,
            "highest_shell_km": self.highest_shell_km,
            "analysis_ready_only": self.analysis_ready_only,
            "min_satellites": self.min_satellites,
        }


COVERAGE_SQL: Final = """
select
    min(epoch_date)                 as first_date,
    max(epoch_date)                 as last_date,
    count(*)                        as satellite_days,
    count(distinct norad_id)        as satellites,
    list(distinct generation)       as generations,
    min(altitude_shell_km) filter (where is_analysis_ready) as lowest_shell_km,
    max(altitude_shell_km) filter (where is_analysis_ready) as highest_shell_km
from fct_daily_decay
where generation <> 'unknown'
"""

GENERATIONS_SQL: Final = """
select
    generation,
    label_confidence,
    satellites,
    decayed_pct,
    median_launch_mass_kg,
    median_span_m,
    median_area_to_mass_proxy,
    first_launch_date,
    last_launch_date
from dim_generation
order by first_launch_date
"""

DAILY_DECAY_SQL: Final = """
select
    epoch_date,
    generation,
    count(*)                                                as satellites,
    1000 * median(altitude_rate_km_per_day)                 as median_m_per_day,
    1000 * quantile_cont(altitude_rate_km_per_day, 0.25)    as p25_m_per_day,
    1000 * quantile_cont(altitude_rate_km_per_day, 0.75)    as p75_m_per_day,
    median(mean_altitude_km)                                as median_altitude_km
from fct_daily_decay
where epoch_date between $start and $end
  and list_contains($generations, generation)
  and altitude_shell_km between $lowest_shell_km and $highest_shell_km
  and altitude_rate_km_per_day is not null
  and (is_analysis_ready or not $analysis_ready_only)
group by epoch_date, generation
having count(*) >= $min_satellites
order by generation, epoch_date
"""

SPACE_WEATHER_SQL: Final = """
select
    epoch_date,
    f10_7_sfu,
    kp_max,
    ap_mean,
    dst_min_nt,
    is_storm_day
from fct_space_weather_daily
where epoch_date between $start and $end
order by epoch_date
"""

# Superposed epoch, pooled across storms and shells, weighted by observations.
# `excess` subtracts each storm's own pre-peak mean for that generation and
# shell, which removes the generation's background trend -- station-keeping,
# commanded descent -- and leaves the response to the storm itself.
STORM_RESPONSE_SQL: Final = """
with epochs as (
    select *
    from fct_storm_epoch
    where list_contains($generations, generation)
      and altitude_shell_km between $lowest_shell_km and $highest_shell_km
      and peak_date between $start and $end
      and peak_dst_nt <= $max_peak_dst_nt
),

baselines as (
    select
        storm_id,
        generation,
        altitude_shell_km,
        sum(mean_altitude_rate_km_per_day * observations) / sum(observations) as baseline
    from epochs
    where days_from_peak < 0
    group by storm_id, generation, altitude_shell_km
)

select
    epochs.generation,
    epochs.days_from_peak,
    count(distinct epochs.storm_id)                                     as storms,
    sum(epochs.observations)::bigint                                    as observations,
    1000 * sum(epochs.mean_altitude_rate_km_per_day * epochs.observations)
        / sum(epochs.observations)                                      as mean_m_per_day,
    1000 * sum((epochs.mean_altitude_rate_km_per_day - baselines.baseline)
               * epochs.observations)
        / sum(case when baselines.baseline is not null
                   then epochs.observations end)                        as excess_m_per_day
from epochs
left join baselines
    using (storm_id, generation, altitude_shell_km)
group by epochs.generation, epochs.days_from_peak
order by epochs.generation, epochs.days_from_peak
"""


def read(database: Path, sql: str, params: dict[str, Any] | None = None) -> pl.DataFrame:
    """Run one query on a connection that is opened, used and closed here."""
    if not database.exists():
        raise WarehouseUnavailable(
            f"No warehouse at {database}. Build one with `uv run starlink-drag demo`."
        )
    try:
        with duckdb.connect(str(database), read_only=True) as connection:
            return connection.execute(sql, params).pl()
    except duckdb.IOException as error:
        raise WarehouseUnavailable(
            f"The warehouse at {database} is locked, most likely by a dbt build in "
            "progress. Reload the page when it finishes."
        ) from error
    except duckdb.CatalogException as error:
        raise WarehouseUnavailable(
            f"The warehouse at {database} has no gold marts yet. Build them with "
            "`uv run starlink-drag demo`."
        ) from error


def coverage(database: Path) -> Coverage:
    row = read(database, COVERAGE_SQL).row(0, named=True)
    if row["first_date"] is None:
        raise WarehouseUnavailable(
            f"The warehouse at {database} has no decay data yet. Build it with "
            "`uv run starlink-drag demo`."
        )
    return Coverage(
        first_date=row["first_date"],
        last_date=row["last_date"],
        satellite_days=row["satellite_days"],
        satellites=row["satellites"],
        generations=tuple(sorted(row["generations"])),
        lowest_shell_km=float(row["lowest_shell_km"] or 0.0),
        highest_shell_km=float(row["highest_shell_km"] or 0.0),
    )


def generations(database: Path) -> pl.DataFrame:
    return read(database, GENERATIONS_SQL)


def daily_decay(database: Path, part: Slice) -> pl.DataFrame:
    return read(database, DAILY_DECAY_SQL, part.params())


def space_weather(database: Path, part: Slice) -> pl.DataFrame:
    return read(database, SPACE_WEATHER_SQL, {"start": part.start, "end": part.end})


def storm_response(database: Path, part: Slice, max_peak_dst_nt: int) -> pl.DataFrame:
    params = {
        key: value
        for key, value in part.params().items()
        if key not in ("analysis_ready_only", "min_satellites")
    }
    return read(database, STORM_RESPONSE_SQL, {**params, "max_peak_dst_nt": max_peak_dst_nt})
