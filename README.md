# Starlink Differential Drag Atlas

Do different Starlink hardware generations — v1.0, v1.5, v2-mini and v2-mini DTC,
which differ substantially in mass and cross-sectional area — show statistically
distinguishable orbital-decay sensitivity to the same space-weather forcing
(F10.7, Ap, Kp, Dst) over Solar Cycle 25?

This repository is the pipeline that answers it: a partitioned, idempotent,
reproducible path from two public APIs to a set of gold marts, a Streamlit
explorer, and a regression that controls for the obvious confounders.

> **Status: Phase 0 (scaffold).** The structure, packaging, tooling and CI are in
> place. Ingestion, transformation and orchestration are not yet built. See
> [the build order](#build-order) for what lands when.

## Quick start

```bash
# uv is the only prerequisite: https://docs.astral.sh/uv/getting-started/
make setup          # create the venv, install everything, install git hooks
make lint           # ruff + ruff-format + mypy
make test           # pytest; never touches the network
```

`make setup` copies `.env.example` to `.env` if you do not have one. Fill in your
[Space-Track](https://www.space-track.org/auth/createAccount) credentials before
running any ingestion. OMNI needs no authentication.

## Stack

| Layer | Tool | Why |
| --- | --- | --- |
| Packaging | uv, `src/` layout | Reproducible lockfile; fast enough to run on every CI push. See [ADR-0001](docs/adr/0001-packaging-with-uv.md). |
| Ingestion | dlt | Incremental loading with state, so a resumed backfill does not re-fetch. |
| Lake | Iceberg on Parquet (MinIO / Cloudflare R2) | Append-only bronze with snapshot isolation and partition-level replacement. |
| Engine | DuckDB + Polars | The dataset fits on one machine; a cluster would be cost and complexity with no benefit. |
| Transformation | dbt-core + dbt-duckdb | Tested, documented, versioned SQL with a lineage graph a reviewer can read. |
| Orchestration | Dagster | Partitioned assets make backfill and per-partition idempotency the default. See [ADR-0002](docs/adr/0002-dagster-over-airflow.md). |
| Data quality | Pandera, dbt tests, dbt_expectations, asset checks | Validate at the Python boundary, in the warehouse, and at the asset level. |
| Serving | Streamlit | Reads gold marts only. |

## Layout

```
src/starlink_drag/
  science/   pure functions, zero I/O — orbital mechanics, generation labelling
  clients/   the only modules that make network calls
  schemas/   Pandera contracts applied at the ingestion boundary
  defs/      Dagster wiring only; no business logic
transform/   the dbt project (staging → intermediate → marts)
analysis/    notebooks, statistical models and figure generation
```

Medallion layers are dbt **tags**, not folders: staging and intermediate are
`silver`, marts are `gold`, and bronze is the raw Iceberg landing zone outside
dbt.

`analysis/` may import from `src/`. `src/` never imports from `analysis/`.

## Data and licensing

Code is MIT licensed. Raw Space-Track data is **not** redistributed — it is
covered by a US-government data-use agreement. `data/` is gitignored, CI fails if
anything under it is tracked, and a pre-commit hook refuses such a commit.
Derived products are published separately.

NASA OMNI data, obtained via the SPDF HAPI server, is public domain.

## Build order

| Phase | Scope | State |
| --- | --- | --- |
| 0 | Scaffold: packaging, tooling, CI, ADRs | **done** |
| 1 | Ingestion: Space-Track and HAPI clients, dlt into bronze Iceberg | next |
| 2 | Transformation: dbt staging, intermediate, marts | |
| 3 | Orchestration: partitioned Dagster assets, asset checks, CLI parity | |
| 4 | Hardening: coverage, integration tests, nightly CI, runbook, Terraform | |
| 5 | Serving: Streamlit explorer, full README, published dbt docs | |
| 6 | Streaming (optional): Redpanda drag nowcast | |
| 7 | Analysis: per-generation regression with bootstrap CIs, figures | |

A full README — architecture diagram, sample output, cost, trade-offs — is
written in Phase 5, once there is something to show.

## Documentation

Every phase is documented twice: once for people who build data pipelines, and
once for people who do not. The plain-language versions use fewer assumed words,
not fewer facts.

| | Technical | Plain language |
| --- | --- | --- |
| What this project is, and why the science is hard | [overview.md](docs/phases/overview.md) | [overview-plain.md](docs/phases/overview-plain.md) |
| Phase 0 — scaffold | [phase-0.md](docs/phases/phase-0.md) | [phase-0-plain.md](docs/phases/phase-0-plain.md) |

- [docs/phases/](docs/phases/) — index, and what each phase document covers
- [docs/adr/](docs/adr/) — architecture decision records, one per decision
