# Phase 3 — Orchestration

Plain-language version: [phase-3-plain.md](phase-3-plain.md).

**Status:** complete, 2026-09-19.

## What this phase is for

Put the pipeline on rails: every artefact a named, dated asset; one command to
rebuild any range; automatic checks that the data is fresh, present and
populated; and retries that survive the kind of failure a multi-hour backfill
inevitably hits.

`defs/` stays thin throughout. Every asset is a few lines calling the same
functions the CLI calls, which is what keeps the orchestrator replaceable.

## The tension at the centre of this phase

The brief asks for a **daily** partition definition. Space-Track's rate limit
makes a *request* per day impossible: six years of daily fetches is roughly
eighty thousand requests, which at fewer than 300 an hour is a week and a half
of wall clock.

The resolution is `BackfillPolicy.single_run()`. A backfill of any range
executes as **one run** that reads its whole `partition_time_window` and fetches
in wide chunks, while still recording each day as its own partition.

So the partition grain is the day and the fetch grain is the window, and the
asset is where the two meet. `tests/integration/test_orchestration.py` asserts
the policy is present, because removing it would turn a thirty-minute backfill
into an eleven-day one without changing a single result.

## The graph

Twenty assets, eighty checks.

```
bronze_satcat ──────┐
                    ├─> warehouse_gp_history ─> stg_spacetrack__gp_history ─┐
bronze_omni ────────┤   warehouse_omni ───────> stg_nasa__omni ─────────────┤
                    │   warehouse_satcat ─────> stg_spacetrack__satcat ─────┤
bronze_gp_history ──┘   warehouse_quarantine                                │
      ^                 warehouse_ingest_audit                              v
      └── depends on bronze_satcat                        int_* ─> dim_* / fct_*
          (the object list comes from the catalogue)
```

Groups are `bronze`, `warehouse`, `silver`, `gold` — the medallion layers,
taken from the dbt tags by the translator rather than restated.

**The `warehouse_*` assets are the reason the graph is correct, not just
pretty.** dbt's sources are DuckDB views, and the views are rebuilt by the
`bronze_views` multi-asset. Mapping dbt's sources onto its keys puts a real
edge between them. Without it, dbt and the view-builder would each depend only
on ingestion, and Dagster would be free to run dbt against stale views.

The first attempt mapped all five sources to a single key, which dagster-dbt
rejects — it requires one Dagster asset per dbt resource. That refusal was
right: collapsing them hid the structure.

## Retries

Two layers, doing different jobs.

Inside `clients/spacetrack.py`, a single flaky response is retried with
exponential backoff and full jitter. That handles a 503.

Above it, `INGEST_RETRY` retries the **whole asset** three times with
exponential backoff and jitter. That handles an expired session, a network drop
mid-backfill, a Space-Track outage — failures no per-request retry can fix.

Both are asserted. `test_a_failing_partition_retries_and_then_recovers`
materialises an asset that fails twice and succeeds on the third attempt, and
requires the run to succeed;
`test_retries_are_bounded_and_a_hopeless_partition_fails` requires a
never-recovering partition to fail after exactly one attempt plus two retries,
rather than retrying forever.

## Concurrency, and a constraint carried from ADR-0003

The rate limiter keeps its window in **process memory**. Two Space-Track assets
running at once would each believe they were within the limit while together
exceeding it — and Space-Track blocks accounts rather than throttling them.

`bronze_satcat` and `bronze_gp_history` therefore share a
`dagster/concurrency_key`. `bronze_omni` deliberately does not: OMNI is public
and unrated. A test pins all three, because the tag is the only thing standing
between a parallel run and a blocked account.

## Asset checks

Four beyond the 76 dbt tests, each answering something a green run cannot.

| Check | Asks |
| --- | --- |
| `gp_history_is_fresh` | Is the newest epoch within three days? Catches a silently stalled ingest. |
| `omni_is_fresh` | Is space weather keeping up with the elements it will be joined to? |
| `fct_daily_decay_has_volume` | Is the mart populated, and is most of it analysis-ready? |
| `fct_daily_decay_null_rate` | Are the columns the analysis needs actually populated? |

They read the warehouse directly rather than trusting run metadata, because the
question is what is *in* the table, not what the last run claimed. All four are
non-blocking: a stale mart is worth seeing, not worth failing a deploy over.

On the verification run, the volume check reported *"2,192,356 rows, 91%
analysis-ready"* and the null-rate check *"all within tolerance"*.

## CLI parity

`starlink-drag backfill` runs the whole thing over a range through Dagster;
`starlink-drag ingest backfill` does the same ingestion without Dagster, for a
plain scheduler. Both call the same functions in `starlink_drag.ingest`.

The Dagster path issues **three** runs rather than one, and the reason is worth
recording: `--partition-range` refuses a selection containing anything
unpartitioned, and the catalogue snapshot and everything downstream of bronze
are unpartitioned by design. So the command runs the catalogue, then the
partitioned sources over the range, then the views and dbt. One command to the
user; three runs underneath, in a fixed order.

## Verification

| Check | Result |
| --- | --- |
| `dagster definitions validate` | passes |
| `starlink-drag backfill` end to end | **exit 0**, all three steps, checks green |
| `dbt build` (as Dagster asset checks) | PASS=89, ERROR=0, 1 deliberate WARN |
| `ruff`, `ruff format`, `mypy --strict` | clean, 51 source files |
| `pytest` | **212 passed**, no network |
| Retry recovers | asserted: two failures then success, run succeeds |
| Retry is bounded | asserted: one attempt plus two retries, then fails |
| Graph shape | asserted edge by edge |

## Three bugs this phase found

**Re-running a backfill broke two staging tests**, and they were right to break.
`stg_nasa__omni` asserted `unique(observed_at)` and `stg_spacetrack__satcat`
asserted uniqueness on `(norad_id, ingest_date)`. Under append-only bronze
(ADR-0005) neither can hold: re-ingesting a covered range leaves a second copy.
Phase 2 deduplicated `gp_history` and **missed OMNI and SATCAT** — the tests
passed only because nothing had been re-run yet. Fixed by adding
`int_omni__deduplicated`, deduplicating the catalogue snapshot inside
`int_satellite__generation_labeled`, and moving the uniqueness assertions to
where they are actually true. Orchestration found this because orchestration is
what re-runs things.

**dbt resolved the warehouse to the wrong file.** `dagster-dbt` runs dbt with
the *project directory* as the working directory; the Makefile runs it from the
repository root. A relative `DUCKDB_PATH` therefore pointed at
`transform/data/atlas.duckdb` under Dagster and `data/atlas.duckdb` under make.
The dbt asset now sets an absolute path before invoking, since it is the one
place that knows both.

**`--select '*'` never reached Dagster.** The shell expanded the asterisk into
the working directory's file list. The selection is now built from the asset
groups found in the definitions, so it contains no glob metacharacter and
cannot go stale when a group is added.

## Deviations from the brief

**dbt assets are unpartitioned.** The brief asks for time-partitioned assets;
the dbt models rebuild from whatever bronze currently holds, because the marts
are full-history aggregates — `fct_storm_epoch` superposes storms across the
entire record, so a per-day rebuild would be meaningless. Ingestion is
partitioned, which is where partitioning buys idempotency.

**`bronze_satcat` is unpartitioned.** It is a snapshot of current catalogue
state, not a time series. Partitioning it by epoch would be a lie about what it
contains.

## Unresolved

**The daily schedule has never fired.** It is defined for 06:00 and validated,
but running it needs a daemon left up for a day. Phase 4's nightly workflow is
where that gets exercised.

**The concurrency tag is not enforced in-process.** Dagster honours
`dagster/concurrency_key` through the daemon and the run queue; a bare
`dagster asset materialize`, which is what `starlink-drag backfill` uses, does
not queue. Today this is safe because the backfill runs its steps sequentially,
but it is an assumption rather than a guarantee, and a future parallel
selection would break it silently.

## Next

Phase 4 — hardening: `science/` coverage above 90%, integration tests against
seeded fixtures, the nightly workflow, the runbook, Terraform for the R2 bucket
and docker-compose for MinIO and Dagster.
