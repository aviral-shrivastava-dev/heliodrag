# What this project is — technical overview

The plain-language version is [overview-plain.md](overview-plain.md). It covers
the same ground with no assumed background.

## Research question

> Do different Starlink hardware generations (v1.0, v1.5, v2-mini, v2-mini DTC),
> which differ substantially in mass and cross-sectional area, show statistically
> distinguishable orbital-decay sensitivity to the same space-weather forcing
> (F10.7, Ap, Kp, Dst) over Solar Cycle 25?

## The observable

Atmospheric drag removes energy from an orbit. A lower-energy orbit has a
shorter period, so **mean motion `n` (revolutions per day) increases** as a
satellite decays. The per-satellite, per-day quantity of interest is therefore
`dn/dt`, derived from consecutive general-perturbations (GP) elements.

Mean motion is preferred over `BSTAR` — the drag-like term carried in the
element set — because `BSTAR` is a *fitted* parameter of the SGP4 propagator. It
absorbs model error, varies with the fitting span, and is not a clean physical
measurement of the satellite. `dn/dt` is noisier per observation but is a direct
consequence of the tracked orbit.

The physical quantity that should separate generations is the **ballistic
coefficient**, `B = m / (C_d · A)`. Per-unit mass and area are not published, so
`B` is not an input — the generations are compared on their measured response,
and the mass/area difference is the hypothesised mechanism rather than a
regressor.

## Sources

| Source | Class | Auth | Constraints |
| --- | --- | --- | --- |
| Space-Track.org | `gp_history` | login | **<30 req/min and <300 req/hr**, enforced by the client. Batch NORAD IDs as comma-delimited lists. |
| Space-Track.org | `satcat` | login | Same limits. Supplies launch date, decay date, object metadata. |
| NASA OMNI (SPDF HAPI) | `OMNI2_H0_MRG1HR` | none | Public domain. F10.7, Ap, Kp, Dst. |

Space-Track data is covered by a **US-government data-use agreement that does
not permit redistribution**. The repository therefore never contains it: `data/`
is gitignored, a pre-commit hook refuses any staged path under it, and CI fails
if anything under it becomes tracked. Derived products are published; extracts
are not.

Under the rate limit, a full 2020→present backfill is a multi-hour job that will
be interrupted. Resumability is a design requirement, not a nicety.

## Identification strategy

A naive correlation of decay rate against F10.7, split by generation, would be
fatally confounded. Three confounders dominate:

**Altitude shell.** Neutral density falls roughly exponentially with height, so
a tens-of-kilometres difference in operating altitude changes drag materially.
Generations do not occupy identical shells.

**Orbit-raising status.** Newly launched satellites are under active propulsion
and climbing. Their observed `dn/dt` reflects SpaceX's flight profile, not the
atmosphere.

**Solar-cycle trend.** Solar activity rose monotonically across most of the
study period, and the newer generations launched later. Generation and epoch are
therefore strongly collinear — the single most dangerous confounder here, and
the one most likely to manufacture a spurious result.

Phase 7 controls for all three explicitly, using per-generation regression or
superposed-epoch analysis with bootstrap confidence intervals. That is the
scientific core; the rest of the repository exists to make its inputs
trustworthy and reproducible.

## Architecture

```
Space-Track gp_history ─┐
Space-Track satcat    ──┤→ clients/ (rate limit, retry) → dlt → bronze Iceberg
NASA OMNI via HAPI    ──┘                                        (append-only)
                                                                      │
                                              DuckDB ← dbt staging ───┘
                                                        (silver)
                                                           │
                                                   dbt intermediate
                                                        (silver)
                                                           │
                                                      dbt marts
                                                        (gold)
                                                       │      │
                                            Streamlit ─┘      └─ analysis/
```

Orchestration is Dagster throughout, with daily time-partitioned assets. The dbt
models are surfaced as Dagster assets via `dagster-dbt`, so ingestion and
transformation share one lineage graph.

## Layering rules

These are enforced by review, and violations are treated as defects.

| Package | Rule |
| --- | --- |
| `science/` | Pure functions, **zero I/O**. All orbital mechanics and generation labelling. Unit tested against known values. |
| `clients/` | The **only** place network calls happen. The Space-Track rate limiter exists in exactly one file. |
| `schemas/` | Pandera contracts, applied at the ingestion boundary. Failures route to a quarantine table rather than crashing the run. |
| `defs/` | Dagster definitions only. Thin wiring, no business logic — so the orchestrator stays replaceable. |
| `config.py` | The only reader of the environment. |
| `analysis/` | May import from `src/`. **`src/` never imports from `analysis/`.** |

dbt layers follow the same discipline: staging (`stg_<source>__<entity>`) is 1:1
with the source and renames and casts only; intermediate
(`int_<entity>__<desc>`) holds business logic and only ever `ref()`s staging,
never a source; marts are `fct_<process>` and `dim_<entity>`, fully tested and
documented.

**Medallion layers are dbt tags, not folders.** Staging and intermediate are
tagged `silver`, marts `gold`, and bronze is the raw Iceberg landing zone
outside dbt entirely. There are no directories named bronze, silver or gold.

## Correctness model

**Partitioning.** Every asset is partitioned by `epoch_date`. Backfills run as
Dagster partition ranges; there are no bespoke backfill scripts.

**Idempotency.** Re-running a partition replaces exactly that partition and
touches nothing else.

**Bronze immutability.** Bronze is append-only with an `ingest_timestamp`.
Deduplication happens in the intermediate layer — by `norad_id + epoch`, keeping
the latest ingest — never by mutating bronze.

> **Open question carried into Phase 1.** "Append-only with an `ingest_timestamp`"
> and "re-running produces byte-identical output" cannot both hold: a re-run
> appends rows bearing a new timestamp, and an Iceberg re-run creates a new
> snapshot besides. The reconciliation assumed here is that bronze appends and
> the *logical* contents after deduplication are what must be reproducible, with
> replacement semantics applying from silver onward. This is flagged rather than
> silently resolved.

## Stack

| Concern | Choice | Rationale |
| --- | --- | --- |
| Packaging | uv, `src/` layout | Committed lockfile for reproducibility; fast enough for every CI push. [ADR-0001](../adr/0001-packaging-with-uv.md) |
| Ingestion | dlt | Incremental loading with state, so a resumed backfill does not re-fetch. |
| Lake | Iceberg on Parquet (MinIO local, R2 prod) | Snapshot isolation and partition-level replacement over an append-only store. |
| Engine | DuckDB + Polars | Single-node dataset; a cluster would add cost and operational surface for no gain. |
| Transformation | dbt-core + dbt-duckdb | Tested, documented, version-controlled SQL with a readable lineage graph. |
| Orchestration | Dagster | Partitioned assets make backfill and per-partition idempotency the default primitive. [ADR-0002](../adr/0002-dagster-over-airflow.md) |
| Quality | Pandera, dbt tests, dbt_expectations, asset checks | Validation at the Python boundary, in the warehouse, and at the asset level. |
| Serving | Streamlit | Reads gold marts only. |
| CLI | typer | Every pipeline operation reachable without Dagster, so the orchestrator stays swappable. |

## Non-negotiables

- No fabricated, synthesised, mocked or placeholder scientific data. If an API is
  unavailable, the build stops and says so.
- No credentials in the repository, including in tests. `.env` locally,
  `.env.example` committed with no values.
- No notebooks in the execution path. They live in `analysis/notebooks/` and are
  never imported by `src/`.
- Type hints on every public function; mypy passes in strict mode.
- Every new module ships with tests in the same commit.
- Every architectural choice gets an ADR.
- No file longer than roughly 300 lines.

## Build order

Eight phases, 0 through 7, built one at a time and reviewed at each boundary.
See [README.md](README.md) for the table and per-phase documents.
