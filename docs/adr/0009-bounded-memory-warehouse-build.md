# ADR-0009: The warehouse build runs in bounded memory

- **Status:** accepted
- **Date:** 2026-09-26

## Context

Once the lake held the full 2020 to 2026 history -- about 30M element sets --
`dbt build` stopped completing on the 16 GB machine it runs on:

1. With DuckDB's defaults, three tests on `int_decay__daily_rates` failed with
   `Out of Memory Error: Allocation failure`. DuckDB assumes it may use 80% of
   RAM (about 12.5 GB) and spills to disk only past that; with other programs
   open, 4.5 GB was actually free, and the operating system refused first.
2. With a 4 GB limit, those tests passed by spilling, but building
   `fct_daily_decay` failed with `failed to pin block` -- on one dbt thread as
   well as four, and with DuckDB's own thread count cut from 16 to 4.
3. Building each silver stage alone showed why. The de-duplication in
   `int_gp__deduplicated` was a window, `row_number() over (partition by
   norad_id, epoch_at order by gp_id desc)`, over every element set, and DuckDB
   could not spill it. The same result written as a `GROUP BY` with
   `arg_max_null` per column finished in 44 seconds within 4 GB.

## Decision

- **DuckDB gets an honest memory limit**, 4 GB by default
  (`DUCKDB_MEMORY_LIMIT`), so it spills to `data/atlas.duckdb.tmp` instead of
  asking the operating system for memory it does not have.
- **dbt builds one model at a time** on the real warehouse, so each gets the
  whole budget. DuckDB already parallelises inside a query.
- **Silver avoids windows over the whole history.** Deduplication is an
  aggregation. The remaining window, the daily `lag` in
  `int_decay__daily_rates`, runs over one row per satellite per day -- about
  11M rows, not 30M -- and fits.
- **The replacement was checked, not assumed.** Row for row on May 2024, the
  aggregation returns exactly what the window did. Across 2024 the rebuilt
  `fct_daily_decay` has the same 2,192,356 rows as before the change. A test
  pins the NULL behaviour that makes `arg_max_null` the right function.

## Alternatives considered

- **Raise the memory limit.** Only moves the ceiling, and on this machine
  there was not 12 GB to give.
- **Materialise the silver models as tables.** Would split the work into
  smaller steps, at about 1.5 GB more disk per build, and the de-duplication
  table's own build hit the same window limit.
- **`preserve_insertion_order = false`,** DuckDB's advice for large writes.
  Tried; it did not fix the failure, so it was not kept.
- **Plain `arg_max`.** Skips rows whose value is NULL, so a correction that
  leaves a field empty would be stitched together with the old element set's
  value. A test fails if it is ever used here.

## Consequences

- The build takes about two minutes on one thread instead of about one and a
  half on four. It completes.
- A future model that windows over the whole element history will hit the same
  wall. The runbook says how to recognise it.
- Where memory is plentiful, raise `DUCKDB_MEMORY_LIMIT`; nothing else changes.
