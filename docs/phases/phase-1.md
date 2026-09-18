# Phase 1 — Ingestion

Plain-language version: [phase-1-plain.md](phase-1-plain.md).

**Status:** complete, 2026-09-18. **Corrected 2026-09-19** — see
[what Phase 2 disproved](#what-phase-2-disproved).

## What this phase is for

Get two real sources onto disk in a form later phases can trust: rate-limited
and resumable, validated at the boundary, and reproducible partition by
partition. No transformation, no science — just landing bronze correctly enough
that nothing downstream has to wonder whether the raw layer is sound.

## What was built

### `clients/spacetrack.py`

The only module that talks to Space-Track, and the only place its rate limiter
exists.

`SlidingWindowRateLimiter` enforces several rolling windows simultaneously —
here `(29, 60s)` and `(299, 3600s)`, one below each published limit. It is not a
token bucket, deliberately: see [ADR-0003](../adr/0003-sliding-window-rate-limiter.md)
for why a bucket sized to the limit admits 58 requests inside a rolling minute.
The clock and sleep function are injected, so the safety property is asserted
over hundreds of simulated requests in milliseconds.

`SpaceTrackClient` adds session login, exponential backoff with **full** jitter
(a fixed multiplier would resynchronise failed batches into lockstep),
`Retry-After` support, and comma-delimited NORAD batching.

### `clients/hapi.py`

NASA OMNI via SPDF. Two behaviours of this server are load-bearing and were
found by capturing real responses, not by reading documentation:

- **Parameters must be requested in the dataset's declared order.** Any other
  order returns HAPI error 1411 instead of data. The order is read from `/info`.
- **Errors arrive with HTTP 200 and are not valid JSON.** A failed request
  returns a stray comma, a status object, then a closing brace — which parses as
  neither CSV nor JSON. Checking `status_code` yields zero rows silently;
  calling `.json()` raises.

Fill markers become `None` here rather than downstream. OMNI writes 999.9 for
"no F10.7 measurement"; letting that through would put a fabricated solar flux
of 999.9 into the science.

### `schemas/`

Pandera contracts over Polars frames, plus the casting that produces them.
Space-Track returns **every** value as a JSON string, including
`"MEAN_MOTION": "15.06402759"` and `"NORAD_CAT_ID": "44713"`, so casting is part
of the boundary rather than an afterthought.

Bounds are deliberately generous: they reject impossible values, not merely
surprising ones. A contract that quarantines a satellite for decaying unusually
fast is worse than no contract. All 12,865 SATCAT records and every GP record
fetched so far validate clean.

`validate()` splits a frame into rows that satisfy the schema and rows that do
not, lazily so every failure is collected in one pass. Failures go to
`bronze_quarantine` with the reason and the whole original payload as JSON. One
bad record never loses the batch, and never disappears silently.

### `ingest/bronze.py`

Partitioned Iceberg tables written through dlt, guaranteeing three things.

**Partition-scoped idempotency.** Merge/upsert keyed on the natural key, with
the partition column declared as an Iceberg partition field. Re-loading one day
replaces it and leaves the rest alone.

**Byte-identical re-runs.** Resources yield Arrow tables, for which dlt's
parquet normaliser defaults `add_dlt_id` and `add_dlt_load_id` to false — both
would be random or clock-derived. Rows are sorted on a stable key; column order
is fixed by the schema module.

**Provenance outside the bytes.** `ingest_timestamp` lives in
`bronze_ingest_audit`, not in bronze rows.
[ADR-0004](../adr/0004-bronze-partition-replace-and-byte-identity.md) explains
why that is the only way the brief's three requirements can coexist.

### `ingest/gp.py`, `omni.py`, `satcat.py` and the CLI

`starlink-drag ingest {satcat,omni,gp,backfill}`. GP fetches wide and partitions
narrow, and a failing chunk is counted and reported rather than abandoning a
multi-hour run.

## What the real APIs turned out to be like

Everything here was measured against the live services.

| Measurement | Value |
| --- | --- |
| Starlink objects in SATCAT | 12,865 — 11,129 on orbit, 1,736 decayed |
| GP records per satellite per day | ≈1.24 |
| Rows in one request, 200 satellites × 365 days | 90,086, in 25s |
| Response row cap | none found at 90k rows |
| Launches by year | 120 (2019) rising to 3,171 (2025) |

The absence of a row cap is what makes the project tractable. One year for all
12,865 objects is a few hundred requests fetched wide, against tens of thousands
fetched day by day — the difference between half an hour and weeks under the
rate limit.

## The write path, and a 20× mistake

The first full-year backfill ran at **304 seconds per chunk**, which extrapolated
to roughly twelve hours. The API call was only 25 seconds of that, so the cost
was in the write. Measuring it directly, with the same 18,000 rows:

| Write | Time | Per partition |
| --- | --- | --- |
| 18,000 rows into 1 partition | 6.7s | 6.67s |
| 18,000 rows into 9 partitions | 17.4s | 1.93s |
| 18,000 rows into 90 partitions | 139.1s | 1.55s |

Cost scales with **partitions touched**, almost independently of row count —
each partition means its own Parquet file plus Iceberg manifest work. A second
measurement showed it roughly doubling again when the target table was not
empty, because the upsert has to scan each partition for matching keys.

The original code landed every NORAD batch separately. With ~37 batches per
window, that rewrote all 366 daily partitions 37 times over: **~13,500 partition
writes instead of 366**. The fix is one write per window rather than one per
batch — accumulate the batches in memory, concatenate, sort, land once.

This is a property worth protecting, so `tests/unit/test_ingest_gp.py` asserts
that N batches across M windows produce N×M requests but only M writes. It is
the kind of thing a later refactor would undo without noticing, because nothing
about the result would look wrong — it would just take twelve hours.

## Verification

| Check | Result |
| --- | --- |
| `ruff`, `ruff format`, `mypy --strict` | clean, 33 source files |
| `pytest` | 63 passed, no network |
| Rate limiter over 700 simulated requests | never exceeds 29/60s or 299/3600s |
| SATCAT validation | 12,865 / 12,865 clean |
| OMNI, one year | 8,784 rows = 366 days × 24 hours |
| Byte-identical partition re-run | **later disproved at scale — see below** |
| Neighbouring partitions after a re-run | digests unchanged |
| Quarantine | impossible values routed out with reason and payload intact |

## What went wrong, and what it cost

**The byte-identity check was wrong the first time.** It hashed every Parquet
file in the partition *directory*. Iceberg does not delete a superseded data
file when a partition is replaced — it stops referencing it, and the file stays
on disk until snapshots are expired. The directory therefore contains rows the
table no longer has.

It surfaced as a test that loaded a partition with corrected values and then
found both the old and new values present. The pipeline was correct; the
verification was not — and it had already produced a "14/14 partitions
byte-identical" result that was not evidence of anything, because comparing
stale files to stale files passes trivially. Bronze is now always read through
the snapshot, in `read_table` and `partition_digest`.

**Iceberg writes `file://C:/...` on Windows**, with two slashes rather than
three. `urlparse` reads `C:` as the *host*, and the returned path silently loses
the drive letter and resolves to a non-existent file. `local_path` puts it back.

**dlt's state filenames are ~100 characters**, which breaks Windows' 260-char
path limit with a bare `FileNotFoundError` under a deep parent. The pipeline
working directory is kept shallow, inside `data/.dlt`.

## What Phase 2 disproved

Two claims in this document were wrong, and correcting them is more useful than
leaving them.

**The write strategy did not survive real volume.** Bronze used a merge/upsert
so that reloading a partition replaced it. Measured at scale, an upsert costs
roughly two hundred times an append — 45,000 rows across 90 partitions took over
twenty minutes against six seconds — and a one-year backfill ran two hours,
finished no window, and stalled. Bronze now **appends**, and duplicates are
collapsed by `int_gp__deduplicated`, which is what the brief specified in the
first place. [ADR-0005](../adr/0005-bronze-appends-rather-than-replaces.md) has
the numbers and what the change costs.

So the acceptance claim above should read: identical input produces
byte-identical Parquet *contents*, but a re-run adds a file rather than
replacing one, so a partition's file *set* is not stable. The reproducible
artefact is the deduplicated model, not the bronze directory.

**The per-partition write cost was misattributed.** This document said writing
costs about 1.5 seconds per partition touched. That was the upsert. An append
writes 90 partitions in six seconds, and the fix described below — one write per
window rather than one per batch — was real but far smaller than the strategy
change that followed it.

The first full-year backfill under the corrected code landed 1,323,708 rows
across 90 partitions in 90 files, in about ten minutes.

## Deviations from the brief, and why

**`src/starlink_drag/ingest/` is a new package.** The brief lists `defs/ingest/`,
but `defs/` is specified as Dagster wiring with no business logic. Fetch
strategy, chunking and landing are business logic, so they live in `ingest/`;
Phase 3's Dagster assets in `defs/ingest/` will call into them.

**Bronze is partitioned by `epoch_date`, not ingest date.** The brief says
ingest date, but per-partition idempotency is a stronger, testable requirement
and needs the partition key to match the re-run unit. SATCAT *is* partitioned by
`ingest_date`, because it is a snapshot of current state rather than a time
series.

**`schemas/decay.py` is not written.** The brief lists it, but nothing in Phase 1
produces decay records — they are derived in Phase 2 from mean-motion evolution.
`schemas/satcat.py` exists instead, because SATCAT is a Phase 1 source and
needed a contract. `decay.py` arrives with the model it validates.

**Space-Track fixtures carry real structure with substituted values.** The brief
asks for real fixtures; the hard constraints forbid redistributing Space-Track
data. The legal constraint wins. Field names, the all-strings typing, null
handling, TLE column layout and decimal precision are exact; NORAD IDs, names
and orbital values are not. See `tests/fixtures/README.md`. OMNI fixtures are
genuine and unmodified, because OMNI is public domain.

## Unresolved

**The limiter is per-process.** Two concurrent processes would each keep their
own window and could exceed the limit together. Phase 3 must not introduce
parallel Space-Track assets without moving that state somewhere shared.

**Superseded rows accumulate.** Under append-only, a re-run leaves the old rows
in place permanently rather than merely unreferenced. Phase 4's retention work
is now load-bearing: it needs compaction as well as snapshot expiry.

**DuckDB's `iceberg_scan` does not work against this lake on Windows**, which
Phase 2 has to design around. Tested directly:

- `iceberg_scan('<table dir>')` fails — no `version-hint.text`, and globbing for
  the latest metadata is disabled by default.
- `iceberg_scan('<metadata.json>')` fails on the manifest URIs, which pyiceberg
  writes as `file://D:/...`. Same two-slash malformation that broke
  `partition_digest`; DuckDB cannot open them either.
- `allow_moved_paths=true`, the documented workaround, does not rescue it.

What **does** work, and is the pattern Phase 2 should use: ask pyiceberg for the
current snapshot's file list, and hand that to DuckDB's `read_parquet`.

```python
files = [local_path(t.file.file_path) for t in table.scan().plan_files()]
con.execute("SELECT ... FROM read_parquet($1)", [files])
```

Verified end to end — 8,784 OMNI rows, and a group-by returning Dst −339 / Kp
8.7 for 10 May 2024 and Dst −406 / Kp 9.0 for 11 May. This is also *more*
correct than a directory glob, because the snapshot excludes superseded files.

The consequence for Phase 2 is that dbt sources cannot be plain SQL over
`iceberg_scan`; the file list has to be resolved in Python and registered as
DuckDB views before dbt runs. That is a design decision for Phase 2, not a
defect here.

## Next

Phase 2 — transformation. Staging models 1:1 with bronze, then
`int_gp__deduplicated`, generation labelling, and decay rates derived from
mean-motion evolution.
