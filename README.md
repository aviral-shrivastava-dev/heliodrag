# Starlink Differential Drag Atlas

A data platform that measures how atmospheric drag on Starlink satellites responds
to space weather, broken out by hardware generation.

The research question comes from a gap in the literature. The engineering problem
is joining a rate-limited multi-million-row orbital history to hourly space-weather indices
and a hardware dimension that no public catalogue actually publishes.

```
                                                   ┌──────────────────┐
  Space-Track  ──► bronze/gp_history  (daily) ──►  │ stg_gp_history   │──┐
  (rate-limited, 45M rows)                         └──────────────────┘  │
                                                                         ▼
  NASA OMNI    ──► bronze/omni       (yearly) ──►  ┌──────────────────┐  ┌──────────────────┐
  (hourly indices, 64k rows)                       │ stg_omni         │─►│ fct_satellite_day│
                                                   └──────────────────┘  └──────────────────┘
                                                                         ▲
  GCAT         ──► generation_map             ──►  ┌──────────────────┐  │
  (12,444 satellites)                              │ dim_satellite    │──┘
                                                   └──────────────────┘

  Python / Dagster              │  dbt + DuckDB
  partitioned, idempotent       │  tested, documented
```

## Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | Dagster | Assets map 1:1 onto tables; daily partitions give per-day retry and resume on a rate-limited backfill. Airflow doesn't run natively on Windows. |
| Transformation | dbt-core + DuckDB | Transformations as tested, documented SQL. DuckDB reads partitioned parquet in place — no load step. |
| Storage | Partitioned Parquet (bronze) → DuckDB (marts) | Hive partitioning by day; `_SUCCESS` markers make completeness explicit. |
| Quality | dbt tests + a validation suite in Python | 46 dbt tests plus 27 unit tests; the generation map is additionally checked against externally known facts. |
| Streaming | Redpanda (Kafka API) | Same API as Kafka, one binary, ~1 GB. |
| CI | GitHub Actions | Full `dbt build` on every PR against a synthetic fixture — no credentials needed. |
| Local stack | Docker Compose | Dagster UI, Redpanda, Redpanda Console. |

## Quickstart

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
```

Run the whole transformation layer offline, with no accounts and no network:

```bash
PYTHONPATH=src python -m starlink_drag.fixtures_cli
cd dbt && DBT_PROFILES_DIR=. dbt deps && DBT_PROFILES_DIR=. dbt build --vars '{gp_history_path: ../data/fixtures/bronze/gp_history, omni_path: ../data/fixtures/bronze/omni, interim_path: ../data/fixtures/interim}'
```

Real data:

```bash
PYTHONPATH=src python -m starlink_drag.build          # GCAT -> generation map
cp .env.example .env                                  # then add Space-Track credentials
DAGSTER_HOME=$PWD/.dagster PYTHONPATH=src dagster dev -m starlink_drag.orchestration.definitions
```

## The engineering problems, and how they're handled

**A rate-limited backfill that will be interrupted.** Space-Track allows 30
requests/minute and 300/hour. Pulling ~2,500 day-windows is an ~8-hour job, so it
*will* be interrupted. Each day is written to a temp file and renamed, then marked
with `_SUCCESS`; a partition without that marker is never trusted. Re-running skips
finished days, so the job is resumable with no external state store. The rate
limiter enforces both ceilings at once — honouring only the per-minute limit still
earns a ban after ~15 minutes, which is covered by a test.

**A dimension no catalogue publishes.** Neither Space-Track nor CelesTrak carries a
hardware-version field. GCAT does carry a spacecraft-bus field plus mass and span,
so the generation is derived there: stated outright for Gen2 (`V2M`/`V2MO`/`V2MD`),
inferred from launch-mass banding for Gen1, with every row recording which. A
validation suite pins the result to independently known facts — v0.9 must be
exactly 60 satellites from a single launch, all reentered. That suite caught a real
bug: the first version banded on dry mass, where one v1.5 variant at 220 kg
collides with v0.9 at 219 kg. Launch mass is cleanly trimodal; dry mass is not.

**A source that lies about success.** HAPI returns errors with HTTP 200 and an
error object in the body, so status codes alone are not enough. It also rejects a
parameter list that isn't in the dataset's own declared order
(`HAPI error 1411: Parameter out of order`) — the loader reads the order from
`/info` at runtime rather than hardcoding it.

**Fill values that quietly poison aggregates.** OMNI encodes "no data" as `999.9`,
`99999` and friends. Bronze keeps them verbatim; `stg_omni` nulls them. A single
unconverted `99999` Dst moves a monthly mean by thousands of nT. Kp is also stored
as Kp×10 and is rescaled in the same model.

**Gaps that invite fabricated data.** Decay rate is a centred difference over
surrounding days, and refuses to span gaps longer than four days rather than
interpolating a slope across a hole. Satellites under thrust are flagged, not
deleted — dropping them would bias the sample toward satellites that finished
orbit-raising early.

**A pipeline that can't be tested without credentials.** `starlink_drag.fixtures`
generates a synthetic bronze tree with the same schema and partitioning as the real
loader, deliberately including republished duplicate epochs, a multi-day gap, an
orbit-raising satellite, and fill sentinels. CI builds and tests every model against
it on every PR.

## Status

| Component | State |
|---|---|
| GCAT → `dim_satellite` | Done. 12,444 satellites, 100% labelled, validated. |
| NASA OMNI → `stg_omni` | Done. 64,165 hourly rows, 2019-05-24 → present. |
| Space-Track → bronze | Backfill running. 249/662 day-partitions, 1.74M satellite-days. |
| dbt models | 9 models across 3 layers, 46 tests, all passing. |
| Dagster | 8 assets, 2 jobs, daily schedule. |
| Streaming | Compose file ready; consumer not yet written. |
| Dashboard | Not started. |

Verified against real data: the OMNI loader reproduces the May 2024 Gannon storm —
minimum Dst **−406 nT at 2024-05-11 02:30Z**, Kp 9.0, ap 400.

## The research question

Oliveira, Zesta & Garcia-Sage (2025) analysed 523 Starlink reentries as a single
cohort and named the missing axis explicitly:

> a superposed epoch analysis using Starlink satellites with different ballistic
> coefficients would enhance the quality of our results

Building `dim_satellite` surfaced two things that reshaped the analysis design.

**Generation is collinear with epoch.** Months both generations were simultaneously
on orbit:

|  | v0.9 | v1.0 | v1.5 | v2-mini | v2-mini-dtc | v2-mini-opt |
|---|---:|---:|---:|---:|---:|---:|
| **v0.9** | 35 | 30 | 8 | **0** | **0** | **0** |
| **v1.0** | 30 | 82 | 60 | 43 | 32 | 22 |
| **v1.5** | 8 | 60 | 60 | 43 | 32 | 22 |
| **v2-mini** | 0 | 43 | 43 | 43 | 32 | 22 |
| **v2-mini-dtc** | 0 | 32 | 32 | 32 | 32 | 22 |
| **v2-mini-opt** | 0 | 22 | 22 | 22 | 22 | 22 |

v0.9 and the V2 family never coexisted, so any drag difference between them is
entirely solar-cycle artefact. No regression control fixes a zero-overlap
comparison.

**But the V2 variants are a natural experiment.** All three share an identical 29 m
span while differing in mass by 1.67×, and flew concurrently for 22–32 months:

| variant | dry mass | span | span²/mass | n | decayed |
|---|---:|---:|---:|---:|---:|
| v2-mini-opt | 530 kg | 29 m | 1.59 | 4,341 | 0.6% |
| v2-mini | 700 kg | 29 m | 1.20 | 2,760 | 7.0% |
| v2-mini-dtc | 910 kg | 29 m | 0.92 | 663 | 3.6% |

Same geometry, same shells, same epoch, same space weather — 1.7× spread in
area-to-mass. Sample sizes point the same way: only 24 dtc and 24 opt have
reentered, so a reentry-based study can't support this, but a decay-rate study uses
all 7,764 V2 satellites at once. `dim_satellite.in_v2_natural_experiment` flags them.

## Caveats

- `span_m` is solar-array wingspan, not drag cross-section. Starlink flies
  knife-edge on station and open-book when deorbiting, so true drag area is
  attitude-dependent and in no public catalogue. `amr_proxy` compares variants to
  each other under a fixed geometric assumption; it is not an absolute ballistic
  coefficient. Resolving attitude state is the main open methodological problem.
- Gen1 v1.0/v1.5 labels are inferred from mass. Filter on `label_confidence`.
- GCAT masses are per-variant nominal values, so within-variant variation isn't signal.
- Space-Track's data-use agreement permits derived products, not a raw mirror of
  the catalogue. Bronze is gitignored.

## Related work

- Oliveira, Zesta & Garcia-Sage (2025), [arXiv:2505.13752](https://arxiv.org/abs/2505.13752)
  — single cohort; names the ballistic-coefficient split as future work.
- Jankovic (2026), [arXiv:2605.19850](https://arxiv.org/abs/2605.19850) — **closest
  prior work.** Already stratifies by altitude shell *and platform generation*, but
  for SGP4 propagation error, not drag response. Must be explicitly differentiated.
- Fitzpatrick et al. (2026), [Space Weather](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2025SW004611)
  — uses SpaceX–NOAA onboard GNSS telemetry, not public TLEs, v1.0 only.

## Design decisions

dbt's own guide asks that deviations from its conventions be reasoned through and
declared explicitly rather than left implicit. Ours:

**Marts keep `dim_`/`fct_` prefixes.** dbt now recommends naming marts as plain
business entities (`customers.sql`). We use Kimball prefixes because the marts
*are* a star schema and the grain of each is the thing a reader most needs to
know. Consistent throughout; staging and intermediate follow dbt's `stg_`/`int_`.

**Bronze is files, not a warehouse schema.** Sources are declared in
`__sources.yml` and reached through dbt-duckdb's `external_location`, so bronze
stays a plain partitioned-parquet lakehouse while still appearing in dbt lineage.
This keeps the raw layer independent of the warehouse and re-readable by anything
that speaks parquet.

**`int_satellite_daily_state` is incremental with a lookback, not a watermark.**
Space-Track republishes revised element sets for epochs it has already issued, so
a day loaded last week can legitimately change. A strict `> max(date)` filter
would never revisit it and the correction would be lost silently. A trailing
window plus `delete+insert` picks it up, and is idempotent — verified by
re-running and confirming zero duplicate keys.

**Anomalies are labelled, not filtered.** `orbit_regime` and
`is_implausible_decay` classify unconverged post-deployment element sets and
reentering satellites rather than dropping them. Range tests are scoped to
`orbit_regime = 'operational'`, so the tests stay strict where strictness is
meaningful instead of being widened until everything passes.

**Coverage loss is measured, not assumed away.** `fct_catalogue_coverage` counts
how many satellites fail to resolve to a generation each day. The medallion
pattern's known weakness is that bad data travels a long way before anyone
notices; this is the boundary check at the dimension join.

**dev and ci are separate targets.** A fixture build writes to `ci.duckdb` so it
can never overwrite real data.

## Layout

```
src/starlink_drag/
  config.py              paths, layers, credentials, verified API parameters
  generation_map.py      GCAT classification, coverage, validation
  build.py               CLI: build + validate the generation map
  backfill.py            CLI: smoke test, scoped windows, resumable load
  fixtures.py            synthetic bronze for offline dev and CI
  ingest/
    spacetrack.py        rate-limited, resumable GP-history loader
    omni.py              HAPI space-weather loader
  orchestration/
    assets.py            Dagster assets (gp_history is daily-partitioned)
    definitions.py       jobs, schedules, dbt integration

dbt/models/
  staging/
    __sources.yml        bronze + interim sources via external_location
    stg_gp_history.sql   typed, deduplicated view
    stg_omni.sql         fill sentinels nulled, Kp rescaled
  intermediate/
    int_satellite_daily_state.sql   incremental daily aggregation
    int_satellite_decay_rate.sql    centred difference, regime classification
    int_space_weather_daily.sql     daily OMNI rollup
  marts/
    dim_satellite.sql
    fct_satellite_day.sql
    fct_catalogue_coverage.sql      data-quality boundary check
    fct_v2_drag_response.sql        conditioned analysis panel

tests/                   unit tests: classification, rate limiting, partitions
```

Each model has its own `.yml` carrying description and tests.

## Data sources

| Source | Auth | Licence |
|---|---|---|
| [GCAT](https://planet4589.org/space/gcat) (McDowell) | none | CC-BY |
| [NASA OMNI](https://omniweb.gsfc.nasa.gov) via [SPDF HAPI](https://cdaweb.gsfc.nasa.gov/hapi) | none | public, doi:10.48322/1shr-ht18 |
| [Space-Track](https://www.space-track.org) | free account | derived products only |
