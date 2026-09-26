# ADR-0007: Publish the schema and aggregates, never element-level data

- **Status:** accepted
- **Date:** 2026-09-25

## Context

Phase 5's acceptance criterion is that someone who has never seen the
repository can run it from the README alone. The obvious way to make that easy
-- ship a small sample warehouse, or host the explorer -- runs into the one
constraint that cannot be negotiated: Space-Track data is covered by a
US-government user agreement that forbids redistributing it. Only derived
products may be published.

Three artefacts in this phase could leak it:

- a sample dataset or warehouse for newcomers;
- the published dbt docs, whose catalog is read from a warehouse;
- the explorer, if it were hosted, and the README screenshot of it.

## Decision

1. **No data ships with the repository.** A newcomer creates a free
   Space-Track account and ingests their own. `starlink-drag demo` makes that
   one command: it lands the most recent 30 days -- minutes, and a small
   fraction of the hourly rate limit -- models them with the production
   pipeline, and opens the explorer. If credentials are missing it says how to
   get them and stops.
2. **The dbt docs are built from an empty warehouse.** Bronze tables are created
   with the production schema and no rows, the models run with dbt's `--empty`
   flag, and the catalog is read from that. The page has every model, column
   type, test and lineage edge. It has no data because the database it came
   from has none -- a property of how it is built, checked by a test, not a
   promise.
3. **Published images show aggregates only.** The README screenshot shows
   per-generation daily medians and space weather: derived products. Nothing
   published identifies a single satellite's orbit on a given day.
4. **The explorer is not hosted.** It reads `fct_daily_decay`, which holds one
   row per satellite per day -- close enough to the raw elements that hosting it
   publicly would be redistribution. It runs locally, on data the viewer
   fetched under their own account.

## Alternatives considered

- **Ship a small sample of real data.** Redistribution, however small the
  sample. Not available.
- **Ship synthetic data for the demo.** Ruled out by the project's rule against
  synthesising scientific data, and rightly: a newcomer would be exploring
  invented orbits, and every chart would show physics nobody measured. The
  integration tests use synthetic orbits to test code; the explorer must never
  show them as data.
- **Host the explorer publicly.** Redistribution of per-satellite rows.
- **Host an aggregates-only explorer.** Permitted, since per-generation medians
  are derived products, but it needs a separate aggregate mart and a publishing
  step. Worth doing later; not needed for anyone to run the project.
- **Build the docs with `--empty-catalog`.** No database needed at all, but the
  page then has no column types, which is the most useful thing in it.
- **Build the docs from the integration tests' synthetic warehouse.** Would
  work, but ties publishing to test code, and an empty warehouse contains
  strictly less.

## Consequences

- The first run needs a Space-Track account. It is free, but it is a step, and
  it has to be working before the first run. The README says so before
  anything else.
- A reviewer cannot click a link and see the explorer. They see the screenshot,
  the published docs, and the code, or run it themselves in a few minutes.
- The GCAT-derived generation seed *is* committed and loaded into the docs
  warehouse: GCAT is CC-BY, so its derivative may be redistributed with
  attribution, which the seed and the README carry.
