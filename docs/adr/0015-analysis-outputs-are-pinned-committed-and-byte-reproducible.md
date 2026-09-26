# ADR-0015: Analysis outputs are pinned, committed, and reproducible byte for byte

- **Status:** accepted
- **Date:** 2026-09-27

## Context

Phase 7's acceptance: deleting `analysis/figures/` and running one command must
restore every figure *identically*. Three things stood in the way.

- **The warehouse moves.** The nightly schedule appends a day, Space-Track
  publishes corrections that change past element sets, and the GCAT seed is
  refreshed. A figure computed over "everything in the warehouse" changes every
  day, and cannot be restored identically -- or cited.
- **Parallel arithmetic is not repeatable in the last bits.** DuckDB, and
  Polars' grouped aggregations, add floating-point numbers in whatever order
  the threads finish. Earlier the same week, two warehouses built from the same
  lake agreed only to nine significant digits for exactly this reason.
- **The outputs were gitignored.** `analysis/figures/` held only a `.gitkeep`,
  so nothing the analysis produced was published, although the brief asks for
  derived products to be.

## Decision

- **The period is pinned** to 2020-01-01 .. 2026-09-16 (`analysis/models/data.py`),
  the last day with complete space weather when the analysis was first run.
  Extending it is a deliberate change with its own diff, not a side effect of
  the nightly run.
- **Every float is rounded before it leaves a parallel step**: to 10
  significant digits out of DuckDB, 6 in the published tables. Both are orders
  of magnitude below physical resolution, and far above the last-bit noise.
- **Every sort that can tie is stable**, and every bootstrap draws from its own
  random stream, seeded by what it estimates. Adding or removing one estimate
  cannot move another's interval (tested).
- **Figures are matplotlib with the Agg backend and its bundled DejaVu Sans**,
  saved as PNG without the `Software` chunk. The explorer's Altair charts render
  through a browser; a paper's figures must be static files that render the
  same everywhere. The palette is still the explorer's, imported from
  `starlink_drag.serving.charts`.
- **The outputs are committed**, with `SHA256SUMS`. They are aggregates per
  generation, shell, storm and month -- derived products, publishable under
  ADR-0007. No element set, and no per-satellite row, is written.
- **One command**, `python -m analysis.make_figures`, writes all of it, and
  `--check` regenerates into a scratch directory and compares hashes without
  touching the committed files.

## Consequences

- Verified on 2026-09-27: the directory was deleted and regenerated, and all
  15 files -- 7 figures, 7 tables and `SHA256SUMS` -- matched their previous
  hashes. An integration test does the same on
  the fixture warehouse, so it is checked on every push, although CI has no
  real warehouse.
- Identical *given the same warehouse*. Someone rebuilding from Space-Track on
  another day may see corrections Space-Track published in between; the
  period, code and seed are fixed, the upstream archive is not. The data
  availability statement says so.
- Figures regenerated with a different matplotlib release may differ in
  pixels. The lockfile pins it; upgrading is a reviewed change whose effect
  shows in `SHA256SUMS`.
