# Runbook

What to do when the pipeline misbehaves. Every entry below has actually
happened during development; none of it is hypothetical.

**The one thing to know first:** partitions are idempotent and bronze is
append-only, so **re-running is almost always safe**. Duplicates are collapsed
in the intermediate layer, not in bronze. If you are unsure, re-run.

## Quick triage

```bash
starlink-drag doctor              # are credentials present?
starlink-drag config              # what settings is this process using?
starlink-drag check-upstream      # do the APIs still look like we expect?
starlink-drag warehouse sync      # rebuild the views dbt reads
```

| Symptom | Start at |
| --- | --- |
| "credentials are absent" / 401 | [API auth failure](#api-auth-failure) |
| A window reports 0 rows, no error | [Rate-limit exhaustion](#rate-limit-exhaustion) |
| Casts producing nulls; counts drifting down | [Schema drift](#schema-drift) |
| One partition red in Dagster | [Partition failure](#partition-failure) |
| Backfill stopped with no error at all | [The run died](#the-run-died) |
| `dbt build` fails a uniqueness test | [Duplicate rows](#duplicate-rows-after-a-re-run) |
| `FileNotFoundError` with a long path | [Windows path limit](#windows-path-limit) |

---

## API auth failure

**Looks like:** `SpaceTrackAuthError: Space-Track credentials are absent`, or
`could not be renewed`, or every chunk failing at once.

**First, distinguish three different things.**

*Credentials missing.* `starlink-drag doctor` prints `MISS space-track
credentials`. Fill `SPACETRACK_IDENTITY` and `SPACETRACK_PASSWORD` in `.env`.
Note that `Settings` reads `.env` relative to the **working directory**, so
running from a subdirectory silently picks up nothing.

*Session expired mid-run.* Space-Track does not answer an expired session with
401 — it returns **HTTP 200 and the HTML login page**. The client detects that
by content type, re-authenticates once, and retries. If you see `could not be
renewed`, the credentials themselves are being rejected.

*Account blocked.* If login is rejected and the password is definitely right,
assume the account was blocked for exceeding the rate limit. That is not
self-healing; log in via the website to confirm, and read the next section
before re-running anything.

**Never** put credentials on a command line or in a Dagster run config. They
belong in `.env`, which is gitignored, and they are `SecretStr` so they cannot
leak through a log line or a `repr`.

---

## Rate-limit exhaustion

**This is the most dangerous failure in the project, because it does not look
like a failure.**

Space-Track publishes two limits — fewer than 30 requests/minute and fewer than
300/hour — and enforces them by blocking accounts. But when you push near the
limit for a sustained period, the observed behaviour is **not** a 429. It is
**HTTP 200 with an empty JSON array**.

**What that costs, concretely.** A real six-year backfill fetched three quarters
of 2020 correctly and then logged:

```
2020-09-27..2020-12-26 -> 0 rows, 0 partitions
```

No error, no failed chunk, exit code 0. Probing the same window by hand
afterwards returned 30,015 rows for a single batch. The data was there the whole
time; the pipeline had been told "nothing here" and believed it.

**What protects you now.** `ingest_gp` records every window that returns no rows
while other windows return plenty, and the Dagster asset raises on it:

```
N window(s) returned no rows while others returned 12,345: 2020-09-27..2020-12-26.
This is usually Space-Track throttling, which it reports as an empty success.
```

A window that is legitimately empty — backfilling before the first launch —
does **not** trip this, because it only fires when some other window had rows.

**What to do.**

1. Stop. Do not immediately re-run at the same rate; that is what caused it.
2. Wait an hour, so the rolling window clears.
3. Re-run more slowly. The limits are configuration and can be **lowered**
   (never raised — the ceiling is validated at startup):

   ```bash
   SPACETRACK_REQUESTS_PER_MINUTE=18 starlink-drag backfill --start 2020-01-01
   ```

4. Re-run only the affected range if you know it:

   ```bash
   starlink-drag ingest gp --start 2020-09-27 --end 2020-12-26
   ```

**Running two ingestions at once is safe for the limit.** Every request from
every process on this machine is recorded in one ledger,
`data/.spacetrack/ledger.sqlite`, and each process checks it under a lock before
sending (ADR-0008). A retry, a second command or the next Dagster step sees what
the others sent in the last hour and waits. They share one budget, so two runs
at once are each slower, not faster.

This used to be a rule you had to follow. On 2026-09-25 a resume nearly
started a second range the moment the first finished -- separate processes,
separate in-memory limiters -- and on 2026-09-26 a Dagster retry did exactly
that and sent about 350 requests in half an hour. The ledger is the fix.

**The ledger only sees this machine.** The limit belongs to the Space-Track
account, and the nightly contract check on GitHub uses the same account from
elsewhere. That is why the defaults are 25 a minute and 290 an hour rather than
29 and 299: the headroom is for requests the ledger cannot count. If you run
ingestion on two machines, give each its own lower budget:

```bash
SPACETRACK_REQUESTS_PER_HOUR=140 starlink-drag backfill --start 2025-01-01
```

---

## Schema drift

**Looks like:** nothing. That is the problem. If Space-Track renames a field or
NASA retires a parameter, the cast produces nulls, the contract passes them, and
every downstream number is computed from emptier data while the pipeline stays
green.

**Detect it:**

```bash
starlink-drag check-upstream            # both sources
starlink-drag check-upstream --source omni
```

It compares the live response's field names against what the parsers read, and
exits non-zero on a mismatch. The nightly workflow runs it.

**If it reports `EMPTY` rather than `DRIFT`:** Space-Track answered, but with
nothing, so no shape was checked. That is not drift. It is most often
throttling — see [Rate-limit exhaustion](#rate-limit-exhaustion) — so wait an
hour and run it again before changing anything.

The Space-Track check probes five satellites chosen from the live catalogue on
every run: the newest still on orbit and launched over a month ago. It used to
probe one hard-coded satellite, STARLINK-1007, which re-entered on 2024-10-02.
Every run then reported drift that was not there, because a satellite that no
longer exists has no elements. Never hard-code a probe again: any fixed
satellite eventually does the same.

**If it reports drift:**

1. `starlink-drag check-upstream` names the missing fields.
2. Update `FIELD_MAP` in the relevant `schemas/` module and the Pandera column.
3. Update the committed fixture in `tests/fixtures/` so the tests exercise the
   new shape.
4. Re-run the affected range. Rows already landed keep the old shape, so a
   re-run is how the new columns get populated.

**Also watch the fill markers.** OMNI encodes "not measured" as 999.9 for F10.7
and 99999 for Dst. If NASA changes a marker and the parser does not, that
sentinel enters the science as a real value — a solar flux of 999.9 will not
look obviously wrong in an average. `check-upstream` reports the observed fills
for this reason.

**And watch the quarantine.** A sudden rise is schema drift wearing a disguise:

```sql
select failure_reason, count(*) from bronze.quarantine
group by 1 order by 2 desc;
```

---

## Partition failure

**Looks like:** one partition red in the Dagster UI, or `N of M chunks failed`.

**It is usually already handled.** Ingestion assets carry a retry policy: three
attempts, exponential backoff, jittered. The client retries individual requests
underneath that. What you are looking at is a failure that survived both.

**Re-run just that partition:**

```bash
dagster asset materialize -m starlink_drag.definitions \
  --select bronze_gp_history --partition 2024-05-10
```

Or a range:

```bash
starlink-drag backfill --start 2024-05-01 --end 2024-05-31
```

**Why this is safe.** Bronze appends, so the re-run adds a second copy of those
rows rather than corrupting anything. `int_gp__deduplicated` collapses them on
`(norad_id, epoch)`, keeping the highest `gp_id` — which also means a
Space-Track *correction* beats what it corrected.

**A partition that keeps failing** is usually one of: a satellite whose element
set is malformed (check `bronze.quarantine`), an epoch range Space-Track has no
data for (legitimately empty — see above for how to tell), or credentials.

---

## The run died

**Looks like:** the log just stops. No error, no exit line, no Python processes.

A long backfill is a multi-hour foreground process. It dies if the terminal
closes, the session ends, or the machine sleeps. This has happened: a six-year
backfill was killed at the 4th of 27 windows and left no trace beyond a log that
stopped mid-sentence.

**Check whether it is actually dead:**

```bash
tail -3 data/backfill_full.log
grep -c 'exit=' data/backfill_full.log      # 0 means it never finished
```

**Resume.** There is no resume state to corrupt: re-run the same command. Windows
already landed are re-fetched and re-appended, and deduplicated downstream. If
you want to skip them, start from the last window the log reported.

For anything long, run it under something that outlives your shell — `tmux`, a
service, or the Dagster daemon with a schedule.

### The run died of memory, and its retry made things worse

**What happened, on 2026-09-26.** The element fetch held a whole 90-day window
in memory before writing it. At 2025 volumes -- about 12,000 satellites -- that
is about 1.7M element sets, and the write ran out of memory
(`ArrowMemoryError`, `ZSTD compression failed: ... not enough memory`).
Dagster's retry then restarted the step as a new process, with a limiter that
remembered nothing, from the start of the range. About 350 requests went out in
half an hour against a limit of 300 an hour, Space-Track answered the excess
with empty windows, and the run was stopped by hand.

**What protects you now** (ADR-0008):

- The fetch writes whenever it has collected 400,000 rows, so memory is
  bounded by rows, not by how many satellites are in orbit.
- The rate limit is kept in the shared ledger, so a retry cannot exceed it.
- A retry resumes: each run records the windows it has landed in
  `data/checkpoints/gp_history-<run id>.json` and a retry of the same run skips
  them. The file is removed when the run succeeds.
- Space-Track steps retry after 10, 20 and 40 minutes, not 30 seconds, so the
  rolling hour has time to drain.
- A batch dlt left behind after a crash is loaded before any new write, never
  silently traded for it (below).

**If a run still dies,** re-run the same range. The checkpoint is keyed by run
id, so only Dagster's *automatic* retries -- the same run, a new process --
resume from it. Re-executing from the UI or starting a new backfill is a new
run: it re-fetches from the start of its range, which is safe under the shared
limit, only slower. To avoid re-fetching, start the new range where the landed
data ends. Without Dagster:

```bash
starlink-drag ingest gp --start 2025-12-27 --end 2026-09-26
```

**Crash leftovers.** dlt keeps an interrupted load as a pending package, and a
plain run afterwards silently loses one of the two batches: sometimes the new
data (dlt warns *"The data you passed to the run function will not be
extracted"*), sometimes the leftover. `bronze.run_pipeline` now loads the
leftover first and then the new data, and refuses to write if anything is still
pending. If you see that warning in an old log, re-run the window it belonged
to.

## The build runs out of memory

**Looks like:** `dbt build` fails with `Out of Memory Error: Allocation failure`
or `failed to pin block of size ... (3.7 GiB/3.7 GiB used)`.

**What protects you now** (ADR-0009): DuckDB is held to 4 GB and spills to
`data/atlas.duckdb.tmp` beyond it, dbt builds one model at a time, and silver
has no window over the whole element history -- the one that could not spill,
the de-duplication, is now an aggregation.

**If it happens anyway:**

1. Close other memory-hungry programs and re-run; nothing is left half-built.
2. If the machine has memory to give, raise the limit for one build:

   ```bash
   DUCKDB_MEMORY_LIMIT=8GB uv run dbt build --project-dir transform --profiles-dir transform
   ```

3. If a *new* model triggered it, find the stage that cannot spill by building
   each view on its own under the limit. A window over every element set is the
   usual culprit; rewrite it as a `GROUP BY` where the logic allows, and check
   the rewrite returns the same rows before trusting it.

## Duplicate rows after a re-run

**Looks like:** `dbt build` failing a uniqueness test on a staging model.

**That is correct behaviour, and the staging assertion would be the bug.**
Bronze is append-only (ADR-0005), so staging — which is 1:1 with bronze —
legitimately contains duplicates after any re-run. Uniqueness is asserted on the
deduplicated intermediate models, never on staging.

If you add a staging model, do not put a `unique` test on it. Add an
`int_*__deduplicated` model and assert there.

---

## Windows path limit

**Looks like:** a bare `FileNotFoundError` naming a very long path, usually
under `_dlt_pipeline_state`.

dlt's state filenames are around a hundred characters. Under a deep parent they
exceed Windows' 260-character limit and fail with no useful message. The
pipeline directory is kept shallow (`data/.dlt`) for this reason. If you move
the project somewhere deep, or run from a deep temporary directory, this comes
back.

Related: Iceberg writes `file://C:/...` with two slashes, so naive URI parsing
loses the drive letter. `bronze.local_path` handles it; anything new that reads
Iceberg file paths must go through that function rather than `urlparse` directly.

---

## Reading bronze correctly

**Never read bronze by globbing the directory.** Iceberg does not delete a
superseded data file — it stops referencing it and leaves it on disk. A glob
therefore returns rows the table no longer contains and silently mixes old and
new values.

Use `bronze.read_table` / `bronze.partition_digest`, or go through the DuckDB
views, which `warehouse sync` builds from the current snapshot.

```bash
starlink-drag warehouse sync    # after any ingest, before any dbt run
```

`make build` does this for you; a bare `dbt build` does not.

---

## Checks that are failing

Asset checks are **non-blocking on purpose** — a stale mart is worth seeing, not
worth failing a deploy over. What each one means:

| Check | If it fails |
| --- | --- |
| `gp_history_is_fresh` | Ingest has stalled. Nothing is erroring; nothing is arriving. Check the schedule and the rate limit. |
| `omni_is_fresh` | Space weather is lagging the elements it joins to. `dst_min_nt` will be null for recent days. |
| `fct_daily_decay_has_volume` | The mart is thin or mostly unusable. Usually the views were not synced, or a range never landed. |
| `fct_daily_decay_null_rate` | A join stopped resolving. Most often the generation seed is stale relative to newly launched satellites. |

Rebuild the generation seed after a launch campaign:

```bash
starlink-drag seed-generations --refresh
```

### `dim_satellite` is far too small, and every fact fails its relationship test

**Looks like:** `dbt build` fails two tests at once --
`expect_table_row_count_to_be_between_dim_satellite` and
`relationships_fct_daily_decay_norad_id__norad_id__ref_dim_satellite_` with
millions of failing rows -- while `dim_generation`, built a second earlier from
the same model, has the right totals.

**What it was.** On 2026-09-25 `dim_satellite` held 865 satellites instead of
12,892: exactly the contents of the *first* of the twenty Parquet files behind
`bronze.satcat`. A plain `SELECT` of the same SQL returned all 12,892. Under
DuckDB 1.5.5, a latest-snapshot filter (a join or a scalar subquery on
`max(ingest_date)`) combined with `QUALIFY row_number() ... = 1` read only the
first file *inside `CREATE TABLE AS`* -- which is how dbt builds every table.
It was deterministic on the real lake; a small synthetic reproduction did not
trigger it.

**What protects you now.** `int_satellite__generation_labeled` finds the latest
snapshot with `max(ingest_date) over ()` instead, which does not trigger it,
and `assert_dim_satellite_is_the_latest_catalogue` compares the built table
against the catalogue on every build. dbt tests run as plain `SELECT`s, so the
test cannot be fooled by the same bug.

**If a mart ever looks short again,** compare the stored table with a fresh
query of its compiled SQL:

```python
import duckdb

con = duckdb.connect("data/atlas.duckdb", read_only=True)
sql = open("transform/target/compiled/starlink_drag/models/marts/dim_satellite.sql").read()
print(
    con.sql("select count(*) from dim_satellite").fetchone(),
    con.sql(f"select count(*) from ({sql})").fetchone(),
)
```

Two different numbers mean the build, not the data, is wrong. Rebuild that
model and everything downstream: `uv run dbt build --project-dir transform
--profiles-dir transform --select dim_satellite+`.

### The explorer says the warehouse is locked

DuckDB allows one writer. While `dbt build` holds the file, the explorer cannot
open it and says so. Reload when the build finishes. The explorer never holds
the file itself, so it cannot be the cause of a build failing to start
([ADR-0006](adr/0006-explorer-reads-gold-through-short-lived-connections.md)).
