# ADR-0004: Bronze replaces partitions; provenance lives outside the data files

- **Status:** partly superseded by [ADR-0005](0005-bronze-appends-rather-than-replaces.md)
- **Date:** 2026-09-18

> **The write strategy in this record no longer holds.** The merge/upsert
> described below is roughly two hundred times slower than an append at real
> volume and could not complete a one-year backfill; bronze now appends and
> deduplicates downstream. ADR-0005 has the measurements. Everything else here
> — provenance outside the data files, and reading through the snapshot rather
> than the directory — still stands.

## Context

Three requirements in the build brief cannot all hold at once. This was raised
at the end of Phase 0 and restated with Phase 1, so it is resolved here rather
than left implicit.

1. *"Bronze is append-only and immutable, with an `ingest_timestamp`."*
2. *"Re-running a partition replaces exactly that partition and touches nothing
   else."*
3. *"Re-running the same partition produces byte-identical output."*

If a re-run **appends** rows stamped with the time they arrived, then (1)
defeats both (2) — the partition now holds two generations of rows — and (3),
because the new rows carry a different timestamp and therefore different bytes.

There is a second, subtler source of non-determinism. dlt adds `_dlt_id` (random
per row) and `_dlt_load_id` (derived from the load's wall clock) to loaded
tables. Either one makes byte-identity impossible.

The acceptance criterion is the testable contract, so the design is built to
satisfy (2) and (3) exactly, and to preserve the *intent* of (1) — that nothing
is ever destroyed or edited in place — by a different mechanism.

## Decision

**Bronze tables are partitioned by a date column and loaded with a merge/upsert
keyed on the natural key.** Loading `epoch_date=2024-05-10` again replaces that
partition's contents and leaves every other partition untouched.

**Immutability is provided by Iceberg snapshots rather than by appending.** No
data file is ever modified. A re-run writes new files and publishes a new
snapshot; the previous snapshot still exists and still references the previous
files, so any earlier state remains readable and auditable. Nothing is
overwritten in place — which is what "immutable" was protecting.

**`ingest_timestamp` moves out of the data rows into `bronze_ingest_audit`**, a
separate append-only table recording, per load: table name, source, partition
range, row count, quarantined count, and the wall-clock ingest time. Provenance
is fully recorded and queryable; it simply does not sit in bytes that must be
reproducible.

**Determinism is engineered, not hoped for.** Resources yield Arrow tables, for
which dlt's parquet normaliser defaults `add_dlt_id` and `add_dlt_load_id` to
false, so neither column is added. Rows are sorted on a stable key before
writing — Space-Track does not guarantee a stable order between identical
queries — and column order is fixed by the schema module.

## Verification

Measured, not assumed. Loading a partition twice produces identical SHA-256
digests for the Parquet files the current snapshot references, and leaves
neighbouring partitions' digests unchanged. `tests/integration/test_bronze.py`
asserts this, and it was confirmed on real Space-Track partitions.

**The first attempt at this check was wrong and is worth recording.** It hashed
every Parquet file in the partition *directory*. Iceberg does not delete a
superseded data file when a partition is replaced — it stops referencing it, and
the file remains on disk until snapshots are expired. A directory listing
therefore returns rows the table no longer contains, silently mixing a
partition's old and new contents. It made a re-run with *corrected* values look
as though the correction had not applied. Bronze must always be read through the
snapshot, never through the directory; `read_table` and `partition_digest` do,
and the reason is in their docstrings.

## Consequences

- **`int_gp__deduplicated` still has work to do.** Deduplication is no longer
  about picking the latest ingest, because a partition holds one generation. It
  is about Space-Track publishing several element sets for the same satellite
  and epoch within a single response. The Phase 2 model dedupes on
  `norad_id + epoch`, keeping the highest `gp_id`.
- **Storage grows with re-runs.** Superseded files accumulate until snapshots
  are expired. Phase 4 needs a retention policy — expire snapshots older than N
  days and rewrite manifests — or the lake grows without bound under repeated
  backfills.
- **Small files.** One load writes one file per partition it touches, so a
  backfill that fetches 200 satellites at a time across a 90-day window leaves
  many small files per day. Compaction belongs with the retention policy in
  Phase 4.
- **Time travel is available but unused.** Reading an earlier snapshot is the
  audit mechanism that replaces an `ingest_timestamp` column. Nothing in the
  pipeline depends on it yet; the runbook should show how to use it.
- **Byte-identity is a property of the current snapshot's data files**, not of
  the table directory or of the Iceberg metadata. Metadata files carry snapshot
  IDs and timestamps and are expected to differ between runs.
