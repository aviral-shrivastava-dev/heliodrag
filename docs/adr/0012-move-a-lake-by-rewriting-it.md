# ADR-0012: A lake moves between storage backends by being rewritten, not copied

- **Status:** accepted
- **Date:** 2026-09-26

## Context

The full 2020-to-present history lived in the local lake, landed over a week of
rate-limited Space-Track requests. The Docker stack's lake, SeaweedFS, held a
two-day sample downloaded to prove the stack worked. A data platform whose
production-shaped stack only ever saw a sample demonstrates the plumbing and
nothing else; the stack should run on the real history.

Getting the history there has three obvious routes, and two do not work:

- **Copy the files** (`aws s3 sync` or similar). Iceberg's metadata records
  every data file by its absolute location -- here `file://D:/Projects/...`.
  Copied as bytes, the tables in the bucket would still point at the local
  disk, readable nowhere else.
- **Mount the local folder into the containers.** The same absolute Windows
  paths mean nothing inside a Linux container, and Docker Desktop reads
  thousands of small files through a bind mount slowly.
- **Download it again.** Hours of Space-Track requests for data already held.

## Decision

`starlink-drag lake-copy` rewrites the lake: each bronze table is read through
its current snapshot and appended to the target through the pipeline's own
writer, so the target's metadata is native to the target.

- Element sets are copied ten days at a time, about 200,000 rows; the small
  tables in one go. The first version copied a calendar month at a time, and
  on 2026-09-26 the laptop running it hit Windows' virtual-memory limit and
  Docker's VM was reset mid-copy.
- The copy is resumable. Both lakes' rows per day are counted from Iceberg
  metadata once, up front: chunks the target holds in full are skipped, empty
  ones copied, and anything in between stops the copy. Iceberg commits are
  all-or-nothing, so a crash leaves no half-chunks; dlt packages a crash left
  pending are aborted first, since the chunk is re-read from the source anyway.
  The first version asked the target about each chunk as it went, re-reading
  every manifest written so far each time: quadratic in time, and the process
  grew by about 4 GB over 228 chunks until the memory watchdog stopped it.
- After each table, the target snapshot's row count must equal the source's;
  both come from snapshot metadata, so the check costs nothing. That check
  caught a real bug on its first run: s3fs caches directory listings
  in-process, so a read straight after a write saw the table as it was before
  (54 of 180 rows). The lake's filesystem is now uncached.
- The copy works in its own dlt directory, so the local pipeline's state is
  untouched.

## What building on the moved lake found

The copy was checked the hard way: the Docker stack built the warehouse from
it, and every mart was compared with the one built from the local lake.
Row counts and dimensions matched exactly; facts matched to nine significant
digits (floating-point sums in parallel differ in the last bits). The first
build also found two faults the local lake had hidden:

- **Out of local ports.** DuckDB opened an HTTP connection per Parquet file;
  dbt's tests on ~2,500 files each exhausted the container's ephemeral ports.
  `httpfs_connection_caching` now goes on every DuckDB connection to a remote
  lake.
- **Timestamps in the session's time zone.** `epoch_at` differed between the
  two warehouses by exactly 5h30. Staging cast UTC instants with a plain
  `cast(... as timestamp)`, which renders them in the session's zone: the
  laptop's, India. Docker, in UTC, was right. The casts now name UTC, and a
  test builds the fixture warehouse in New York time to keep them honest.
  Every rate, interval and join had been computed from differences and equal
  values under a constant offset, so no number in the marts changed -- only
  the labels, which would have misled anyone aligning epochs by the hour, and
  under daylight saving the offset is not constant.

## Alternatives considered

- **Rewrite the metadata paths in place.** Iceberg has a procedure for this,
  but only in Spark, which this project deliberately does not run.
- **Copy the silver and gold layers too.** They are derived; the target builds
  them from bronze with `dbt build`, which is also the proof that the copied
  bronze is complete enough to build from.

## Consequences

- Any lake can be moved to any S3 target -- SeaweedFS today, R2 later -- with
  no API calls and a verified row count per table.
- Rewriting is slower than a byte copy, and the target's files are not
  byte-identical to the source's, only row-identical. The byte-identity
  guarantee (ADR-0004) is about re-running a load, not about moving a lake.
