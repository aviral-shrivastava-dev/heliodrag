# Phase 0 — Scaffold

Plain-language version: [phase-0-plain.md](phase-0-plain.md).

**Status:** complete, 2026-09-18.

## What this phase is for

Phase 0 builds no pipeline. It establishes the conditions under which every
later phase can be trusted:

- A dependency set that resolves and is pinned, so the same code produces the
  same result in a year.
- Automated checks that fail loudly, so a defect surfaces on commit rather than
  in a mart.
- A place for every kind of file, agreed before there are files to place.
- A record of why the significant choices were made, written while the reasons
  were fresh.

It also front-loads the single largest dependency risk in the stack: Dagster,
dbt-core and dlt pin overlapping libraries and have historically been awkward to
co-install. Discovering that in Phase 0 is cheap; discovering it in Phase 3 is
not.

## Context: this was a restructure, not a greenfield start

The repository already contained an earlier iteration — an Airflow DAG beside
Dagster, a dbt project at `dbt/`, a flat `src/starlink_drag/` — with much of the
working tree already deleted but still tracked in `HEAD`.

That layout was removed to match the specified structure. Everything remains
recoverable from commit `af5d5fa`. Specifically removed: `orchestration/airflow/`,
the root `dbt/` project, `requirements.txt`, the root `Dockerfile` and
`.dockerignore`, both `docker-compose*.yml` files, `docs/decisions/`,
`docs/roadmap.md`, and the old `src/` and `tests/` modules.

**`data/` was not touched.** It holds ~2.7 GB of real prior ingestion — 662
daily bronze partitions of `gp_history` (~5.56M satellite-days), OMNI data, and
a 1.5 GB `warehouse.duckdb`. Under Space-Track's rate limit that represents many
hours of fetching, so it is treated as read-only. Every dbt command run during
verification was pointed at a scratch database via `DUCKDB_PATH`.

## What was built

### Packaging

| File | Purpose |
| --- | --- |
| `pyproject.toml` | Project metadata, the full runtime stack, a `dev` dependency group, and configuration for ruff, mypy, pytest and coverage. |
| `uv.lock` | 186 packages pinned exactly. Committed. CI installs with `--frozen`. |
| `.gitattributes` | `* text=auto eol=lf`. Development is on Windows, CI on Linux; without this, git's autocrlf and the `mixed-line-ending` hook fight each other and the Makefile's tab-indented recipes are at risk. |

Python is pinned `>=3.12,<3.13` and installed by uv itself, so the interpreter
is part of the reproducible environment rather than whatever the host provides.
Build backend is hatchling over the `src/` layout.

### Configuration

`src/starlink_drag/config.py` is the only module that reads the environment.
`pydantic-settings` models are split by prefix:

| Model | Prefix | Holds |
| --- | --- | --- |
| `SpaceTrackSettings` | `SPACETRACK_` | Credentials, base URL, rate limits, retry count, NORAD batch size. |
| `HapiSettings` | `HAPI_` | SPDF base URL, dataset, timeout. |
| `LakeSettings` | `LAKE_` | Backend (`local`/`r2`), endpoint, bucket, credentials, region. |
| `Settings` | — | Paths, pipeline start date, log level; composes the three above. |

Two details that matter later:

**Secrets are `SecretStr`.** Passwords and access keys cannot leak through a
`repr`, a log line or a Dagster run-config dump. `model_dump_json()` renders
them as `**********`, which is what `starlink-drag config` prints.

**Rate limits are validated configuration, not constants.** `requests_per_minute`
is `gt=0, le=29` and `requests_per_hour` is `gt=0, le=299`. They can be lowered
so tests can drive the limiter quickly; they cannot be raised past Space-Track's
published ceiling. Attempting to do so raises a `ValidationError` at startup
rather than getting an account blocked at runtime.

### Entry points

| File | Purpose |
| --- | --- |
| `src/starlink_drag/cli.py` | typer app. `version`, `config` (resolved settings, secrets redacted), `doctor` (reports credential presence without revealing it, exits 1 if incomplete). Installed as the `starlink-drag` script. |
| `src/starlink_drag/definitions.py` | Dagster entry point. Currently an empty `Definitions()` — it loads cleanly and shows an empty graph, which is honest: there is nothing to orchestrate yet. |

The CLI exists from Phase 0 rather than Phase 3 because it is the mechanism that
keeps the orchestrator replaceable, and because `make run` and `make setup`
should not be aspirational.

### Quality gates

`.pre-commit-config.yaml` runs, on every commit:

- `check-added-large-files` (512 KB), `check-merge-conflict`, `check-toml`,
  `check-yaml` (with `--unsafe`, since dbt YAML carries Jinja),
  `end-of-file-fixer`, `mixed-line-ending --fix=lf`, `trailing-whitespace`
- `ruff --fix` and `ruff-format`
- `detect-secrets` against `.secrets.baseline`
- a local `fail` hook that refuses any staged path under `data/`

`.github/workflows/ci.yml` runs on push to `main`/`master`, on pull requests and
on demand: checkout, pinned uv `0.12.16`, `uv python install 3.12`,
`uv sync --all-groups --frozen`, `make lint`, `make test`, then a check that
nothing under `data/` is tracked. CI calls the same Makefile targets a developer
does, so the two cannot drift.

`Makefile` targets: `setup`, `lint`, `format`, `typecheck`, `test`, `test-cov`,
`run`, `docs`, `clean`, `help`. Each is a thin wrapper over uv.

### Tests

`tests/conftest.py` provides an autouse `isolated_environment` fixture. It
`chdir`s every test into an empty temporary directory and strips all
`SPACETRACK_*`, `HAPI_*` and `LAKE_*` variables.

This is load-bearing rather than tidy. The nested settings models are
constructed via `default_factory`, so they resolve `.env` independently of what
the outer `Settings` object is passed. The development machine has a populated
`.env` with live Space-Track credentials. Without the fixture, tests asserting
on default values pass or fail depending on whose laptop they run on — and CI,
which has no credentials, would disagree with local runs.

Ten tests currently cover `config.py` and `cli.py`, including that a password
never appears in a `repr` or on stdout, and that the rate limit cannot be
configured above the ceiling.

### dbt project

`transform/` holds `dbt_project.yml`, `profiles.yml` and `packages.yml`. Models
are empty until Phase 2, but the project parses and `dbt docs generate` runs.

`dbt_project.yml` encodes the medallion layers as tags: `staging` and
`intermediate` are tagged `silver`, `marts` is tagged `gold`. There are no
folders named bronze, silver or gold, and bronze is outside dbt entirely.

`profiles.yml` is committed deliberately — it contains no secrets. The DuckDB
path comes from `DUCKDB_PATH`, defaulting to `data/warehouse.duckdb`, and
resolves against the repository root because the Makefile always invokes dbt
from there. A separate `ci` target points at an ephemeral database.

### Documentation

`docs/adr/` holds a template and index plus two records:

- **ADR-0001, packaging with uv.** Covers the reproducibility requirement, the
  `src/` layout, and why Poetry, pip-tools, conda and Python 3.13 were rejected.
- **ADR-0002, Dagster over Airflow.** States the case for Airflow plainly,
  including that it appears in far more job postings and that a reviewer may
  read its absence as unfamiliarity rather than as a decision. The mitigation is
  that `cli.py` exposes every operation, so any scheduler could drive the same
  code paths.

`docs/phases/` holds this document set. `docs/architecture.md`,
`architecture.mmd`, `data_dictionary.md` and `runbook.md` are stubs naming the
phase that writes them.

## Verification

Every command below was run, and these are the results observed. `make` is not
installed on the development machine, so each Makefile recipe was executed
directly rather than through `make`.

| Check | Result |
| --- | --- |
| `uv sync --all-groups` | 186 packages, resolved first attempt, no conflicts |
| `uv sync --all-groups --frozen` | passes — the lockfile is complete, so CI will not have to resolve |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 26 files already formatted |
| `mypy` (strict) | Success: no issues found in 19 source files |
| `pytest` | 10 passed |
| `dagster definitions validate -m starlink_drag.definitions` | Validation successful for code location |
| `dbt deps` + `dbt docs generate` | catalog written (against a scratch DB) |
| `pre-commit run --all-files` | all hooks pass |
| `pre-commit run no-data-directory --files data/...` | fails as intended |

Resolved versions of the stack that matters:

```
python 3.12.14   uv 0.12.16
dagster 1.13.23  dagster-dbt (from lock)
dbt-core 1.12.5  dbt-duckdb 1.11.0
dlt 1.30.0       duckdb 1.5.5
pandera 0.33.1   polars 1.44.2
pyiceberg 0.12.0 pydantic 2.13.5
streamlit 1.64.0 typer 0.27.2
```

### What could not be verified

**`make setup && make lint && make test` was not run as written**, because GNU
make is absent on this Windows machine. The recipes were verified individually.
Install with `winget install GnuWin32.Make` to close this.

**CI has never executed.** The repository has no git remote. The workflow YAML
is valid and the commands it runs are verified, but "CI is green" is not yet a
statement anyone can make.

## Decisions taken during the build

**`make test` on a genuinely empty suite fails.** pytest exits 5 when it
collects nothing, which would fail CI. Rather than special-casing the exit code,
`config.py` and `cli.py` were pulled forward from Phase 1 and given real tests.
This is a deliberate deviation from a strict reading of "passes on an empty
suite": the spirit is a green pipeline with nothing meaningful yet built.

**mypy checks `src` and `tests` only.** Listing `analysis` fails with *"There
are no .py[i] files in directory"* until it contains modules. It rejoins the
list in Phase 7; the `pyproject.toml` comment records why.

**dbt flag placement.** In dbt ≥1.9, `--project-dir` and `--profiles-dir` are
subcommand options, not global ones. The Makefile passes them after the
subcommand. The first attempt did not, and failed with *"No such option
'--project-dir'"*.

**Test-literal passwords carry `# pragma: allowlist secret`.** detect-secrets
correctly flagged them. They are annotated rather than removed, because the
tests they belong to are the ones proving secrets do not leak.

## Deliberately absent

These are not oversights. Each arrives with its phase.

| Missing | Phase |
| --- | --- |
| `clients/spacetrack.py`, `clients/hapi.py` | 1 |
| `schemas/gp.py`, `omni.py`, `decay.py` | 1 |
| `science/orbital.py`, `generations.py` | 2 |
| dbt models, seeds, macros | 2 |
| `defs/resources.py`, `defs/partitions.py`, assets, checks | 3 |
| `.github/workflows/nightly.yml`, Terraform, docker-compose | 4 |
| `app/streamlit_app.py`, full README, architecture diagram | 5 |
| `analysis/make_figures.py`, `analysis/models/`, `CITATION.cff` | 7 |

## Unresolved, carried into Phase 1

**Bronze semantics are self-contradictory as specified.** "Bronze is append-only
and immutable, with an `ingest_timestamp`", "re-running a partition replaces
exactly that partition", and "re-running produces byte-identical output" cannot
all hold. A re-run appends rows with a new timestamp; Iceberg additionally
creates a new snapshot; and Parquet output is not byte-stable regardless. The
assumed reconciliation is that bronze appends, dedup in `int_gp__deduplicated`
produces the reproducible logical result, and replacement semantics apply from
silver onward — but this needs confirming before the dlt write disposition is
chosen.

**The Iceberg-to-DuckDB read path is unproven.** dbt-duckdb reads Iceberg
through DuckDB's `iceberg` extension, which is read-only and historically
particular about metadata paths. Phase 1 should prove this on one real partition
rather than let Phase 2 discover it.

## Next

Phase 1 — ingestion. The rate limiter is built before the first real API call,
not after.
