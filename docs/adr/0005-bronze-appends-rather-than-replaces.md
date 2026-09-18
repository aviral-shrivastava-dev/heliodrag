# ADR-0005: Bronze appends rather than replaces partitions

- **Status:** accepted
- **Date:** 2026-09-19
- **Supersedes:** the write-strategy decision in
  [ADR-0004](0004-bronze-partition-replace-and-byte-identity.md). The rest of
  that record — provenance outside the data files, reading through the snapshot
  rather than the directory — still stands.

## Context

ADR-0004 chose a merge/upsert so that reloading a partition replaced its
contents, which satisfied the acceptance criterion *"re-running the same
partition produces byte-identical output"* exactly.

It was verified on partitions of a few thousand rows and shipped. At real volume
it does not work. A one-year backfill ran for two hours, completed no window,
and then stalled with no file written for minutes at a time.

Measured directly, writing the same rows with each strategy:

| Rows | Partitions | `append` | `merge`/`upsert` |
| --- | --- | --- | --- |
| 45,000 | 90 | **6.0s** (3.7s on re-run) | **>20 min**, killed unfinished |
| 18,000 | 90 | — | 139s |
| 18,000 | 9 | — | 17s |
| 18,000 | 1 | — | 6.7s |

Two things follow. The cost of an upsert scales with **partitions touched per
commit** *and* with **rows**, so no choice of batch size rescues it: batching
narrowly multiplies the commits, batching widely multiplies the work per commit.
And the ~1.5s per partition attributed to "the write" in the Phase 1 write-up
was the upsert, not the write — `append` does 90 partitions in six seconds.

Alternatives inside dlt were explored and are closed:

- **`delete-insert`** is rejected by dlt's Iceberg destination, which offers
  only `upsert` and `insert-only`.
- **Deleting partitions through pyiceberg directly**, then appending, does not
  commit. The table has to be registered into a writable catalog because dlt
  uses an in-memory one; four predicate forms (`In` with dates, `EqualTo` with a
  date, with a string, and a SQL row filter) each matched zero rows and left the
  table unchanged.

## Decision

**Bronze is append-only**, which is what the build brief specified in the first
place: *"Bronze is append-only and immutable, with an ingest_timestamp. Dedupe in
the intermediate layer, never by mutating bronze."*

Re-running a partition leaves a second copy of its rows. `int_gp__deduplicated`
collapses them on `(norad_id, epoch)`, keeping the highest `gp_id`. That key does
double duty: identical copies collapse, and a Space-Track *correction* — which
carries a new `gp_id` — wins over what it corrected.

## What is gained and what is lost

**Kept.** Identical input still produces byte-identical Parquet *contents*:
resources yield Arrow tables so dlt adds no `_dlt_id` or `_dlt_load_id`, rows are
sorted on a stable key, and no wall-clock value sits in a data column. Two runs
over the same rows hash the same.

**Lost.** A partition's *file set* is no longer stable. A re-run adds a file
rather than replacing one, so the strict reading of "re-running produces
byte-identical output" no longer holds, and this record is where that is
admitted rather than quietly reinterpreted. The reproducible artefact is the
deduplicated intermediate model, not the bronze directory.

**Also lost.** For `gp_history` a correction is resolvable because `gp_id`
orders it. OMNI carries no equivalent marker, so if NASA revises a past value,
bronze holds both and nothing distinguishes them. Correcting OMNI therefore means
reloading the table, not a partition. `tests/integration/test_bronze.py` asserts
this limitation explicitly so it cannot surprise anyone later.

## Consequences

- Storage grows with every re-run, and faster than under ADR-0004 because
  superseded rows are never unreferenced. Phase 4's retention work is now
  load-bearing rather than tidy-up: it needs compaction *and* snapshot expiry.
- Every model reading bronze must go through the deduplicating intermediate
  layer. Reading `stg_spacetrack__gp_history` directly will double-count after
  any re-run, so the staging model exists only to feed
  `int_gp__deduplicated`.
- The Phase 1 documents were written against ADR-0004 and overstate the
  guarantee. They have been corrected rather than left to contradict this.
- A future Iceberg or dlt release may make partition replacement cheap. If it
  does, this is worth revisiting — the acceptance criterion as originally worded
  is a better guarantee than the one in force, and it was given up on cost, not
  on principle.
