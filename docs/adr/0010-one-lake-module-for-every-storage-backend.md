# ADR-0010: One lake module, so every tool reaches S3 storage the same way

- **Status:** accepted
- **Date:** 2026-09-26

## Context

The project was meant to run on two kinds of storage: a directory on the local
machine, and an S3-compatible bucket -- MinIO from `infra/docker` to rehearse
production, Cloudflare R2 in production. `LAKE_BACKEND` chose between them, and
the settings held an endpoint and keys.

On 2026-09-26 it turned out that only the local directory had ever worked.
Nothing passed the endpoint or the keys to anything. The Docker stack started,
its web page answered, and Phase 4 recorded that as verified -- but the stack
could not store a row, and the web page was showing the project failing to load.
Running the pipeline inside it found five separate faults:

1. dlt, pyiceberg and DuckDB were never given the endpoint or keys.
2. The Docker image had no dbt manifest, so Dagster could not load the project.
3. Space-Track credentials never reached the containers: Compose looks for
   `.env` beside the compose file, not at the repository root.
4. The asset checks open their own DuckDB connection to bronze, without keys.
5. NASA's HAPI answers a request for data it has not published yet with status
   1201, "OK - no data for time range", which the client treated as an error.
   OMNI is published about a week behind, so every daily run would have failed.
   That one had nothing to do with Docker; Docker was simply the first thing to
   ask for yesterday.

## Decision

- **`starlink_drag.lake` builds every tool's view of the lake from one set of
  settings:** dlt's destination and credentials, pyiceberg's FileIO properties,
  an fsspec filesystem for listing and hashing files, DuckDB's `CREATE SECRET`,
  and the environment variables the dbt profile turns into the same secret. No
  other module reads `LAKE_*` settings, so the shapes cannot drift apart.
- **Local paths stay exactly as they were.** The local branch is the proven one
  and keeps its Windows path handling; only the remote branch is new.
- **Nothing reads the lake by listing a directory.** The last place that did,
  the SATCAT lookup, reads through the table's snapshot like everything else.
- **The image is buildable into a working stack:** it runs `dbt deps` and
  `dbt parse` and installs DuckDB's S3 extension at build time, and both
  Dagster services read credentials from the repository's `.env`.
- **HAPI 1201 means zero rows.** Every other non-1200 status is still an error.
- **It is tested against a real S3 API, in CI.** A CI job starts MinIO and
  lands the synthetic fixture in it, syncs the views and runs the real
  `dbt build` against it.

## Alternatives considered

- **Keep S3 as future work and remove it from the docs.** Honest, but it would
  leave `infra/docker` as a stack that starts and does nothing, and the path to
  R2 untested until the day it was needed.
- **Run MinIO but keep the lake on local disk inside the containers.** Would
  make the Docker stack work while testing nothing about object storage, which
  is the reason MinIO is there.
- **Persistent DuckDB secrets** instead of a secret per connection. They are
  stored as plain files outside the project, and dbt would still need to find
  them. The profile's `secrets` block is what dbt-duckdb provides for this.
- **A service-container MinIO in CI.** GitHub's service containers cannot be
  given a command, and MinIO needs `server /data`, so CI starts it with
  `docker run`.

## Consequences

- `LAKE_BACKEND=r2` works for MinIO, and is the same code path R2 will use. R2
  itself is still not deployed: that needs a Cloudflare account and token.
- Each machine keeps its own rate-limit ledger. The Docker stack's ledger lives
  in its own volume, so running ingestion on the host and in Docker at the same
  time would share the account's limit without either knowing. The runbook says
  not to.
- The CI job needs Docker on the runner; GitHub's Ubuntu runners have it.
- Checking that containers start is not checking that data flows. This record
  exists because that distinction was missed once.
