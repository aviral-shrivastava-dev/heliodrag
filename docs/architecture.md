# Architecture

## Data flow

```
 SOURCE              BRONZE                  STAGING           INTERMEDIATE           MARTS
 (external)          (parquet on disk)       (views)           (tables)               (tables)

 Space-Track   ──►   gp_history/        ──►  stg_gp_history ─► int_satellite_    ─┐
 30 req/min          epoch_date=.../         typed,            daily_state        │
 300 req/hour        data.parquet            deduplicated      (incremental)      │
                     _SUCCESS                                       │             │
                                                                    ▼             ├─► fct_satellite_day
                                                              int_satellite_      │   one row per
 NASA OMNI     ──►   omni/              ──►  stg_omni       ─► decay_rate         │   satellite per day
 HAPI, no auth       year=YYYY/              fills nulled,                        │        │
                     data.parquet            Kp rescaled  ──► int_space_weather_ ─┤        ▼
                                                              daily               │   fct_v2_drag_response
                                                                                  │   analysis panel
 GCAT          ──►   generation_map     ──────────────────────► dim_satellite ────┤
 McDowell, CC-BY     .parquet                                                     └─► fct_catalogue_coverage
 built in Python:                                                                     quality boundary check
 mass banding +
 validation suite
```

## Why the layers are where they are

**Bronze is files, not a warehouse schema.** Append-only partitioned parquet,
never edited in place. dbt reaches it through `external_location` sources, so it
stays readable by anything that speaks parquet while still appearing in lineage.

**Staging is views.** Thin renaming and typing only. Materialising it would
double the storage of the largest table in the project for no gain.

**Intermediate is where the derivations live.** Daily aggregation, the
centred-difference decay rate, the space-weather rollup. Marts became thin joins
once this layer existed.

**Marts are the business entities**, in star-schema form: one dimension, three
facts.

## Orchestration

Eight Dagster assets. `gp_history` is daily-partitioned — the reason an
orchestrator is here at all, since a 662-day backfill against a rate-limited API
needs per-day retry and resume. Everything else is unpartitioned and cheap.

## Testing strategy

| Layer | Mechanism | What it catches |
|---|---|---|
| Python units | pytest, 27 tests | rate-limiter arithmetic, partition atomicity, classification logic |
| Generation map | bespoke validation suite | a silently wrong join — pinned to externally known facts |
| dbt | 54 tests | grain, referential integrity, value ranges, accepted values |
| Coverage | `fct_catalogue_coverage` + singular test | dimension rows lost at the join boundary |
| CI | GitHub Actions | full `dbt build` against a synthetic fixture, no credentials |

The fixture deliberately contains republished duplicate epochs, a multi-day gap,
an orbit-raising satellite and fill sentinels, so the awkward paths are exercised
on every pull request rather than only against live data.

## Known limitations

See the README. The significant one: operational Starlink satellites station-keep,
so `decay_rate_km_per_day` measures the residual after thrust rather than
atmospheric drag.

## Decision records

- [1. Dagster over Airflow](decisions/0001-dagster-over-airflow.md)
- [2. Incremental loading with a lookback, not a watermark](decisions/0002-incremental-lookback-not-watermark.md)
- [3. Label anomalous records rather than filter them](decisions/0003-label-anomalies-rather-than-filter.md)
