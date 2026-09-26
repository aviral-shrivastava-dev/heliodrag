# ADR-0006: The explorer reads gold marts only, through short-lived connections

- **Status:** accepted
- **Date:** 2026-09-25

## Context

Phase 5 adds a Streamlit explorer over the warehouse. Two questions about how
it reads, both of which have a tempting wrong answer.

**Which tables.** The silver layer has everything the explorer could want, and
some of it more conveniently shaped -- daily space weather existed only as
`int_space_weather__daily`. But silver is the part of the project that is free
to change: staging and intermediate models are rewritten whenever a cleaning
rule changes. A consumer that reads them breaks when they do, and nothing
warns it.

**How long to hold the database.** DuckDB allows one writer per file, and a
reader that holds the file open prevents a writer from opening it. The natural
Streamlit idiom -- one connection cached with `st.cache_resource` for the life
of the server -- would therefore make the next `dbt build` fail with a lock
error for as long as anyone had the explorer open.

## Decision

1. **Gold only.** `serving/marts.py` names the gold marts in `GOLD_MARTS`. A
   unit test scans every query for the tables it reads and fails on anything
   else; another asserts `GOLD_MARTS` equals the set of models dbt tags
   `gold`. Where the explorer needed something gold lacked, gold grew:
   `fct_space_weather_daily` was added as a mart, with its own tests, rather
   than read from silver.
2. **Short-lived, read-only connections.** Each query opens the file
   read-only, runs, and closes. Results -- not connections -- are cached in the
   app, for ten minutes. If a build holds the lock at that moment, the explorer
   says so and asks for a reload, instead of failing.
3. **Queries and charts live in the package.** `app/streamlit_app.py` is
   layout only. The SQL and the Altair specs are in `src/starlink_drag/serving`,
   typed and tested like the rest of the code, and the whole script is run
   headless in the integration suite with Streamlit's `AppTest`.

## Alternatives considered

- **Read silver where it is convenient.** Lost because silver is where cleaning
  rules change; a consumer of it breaks silently when they do.
- **One connection for the life of the server** (`st.cache_resource`), the
  usual Streamlit pattern. Lost because it blocks every `dbt build` while the
  explorer is open.
- **An API service between the explorer and the warehouse.** Would isolate the
  two, at the price of a second service to run, deploy and test for a single
  consumer. A read-only connection gives the same isolation.
- **Export gold to Parquet for the explorer to read.** A copy step that can
  silently fall behind the warehouse it copies.

## Consequences

- The gold layer is now a contract with a consumer, which is what it was
  claimed to be. Changing a mart's shape breaks a test that names the reason.
- Opening a connection per query costs a few milliseconds. The expensive part
  is the aggregation, which is cached.
- The explorer can be open while the pipeline runs. During the seconds a build
  holds the lock it shows a message, not a stack trace.
- A mart that exists only for serving must earn its place in gold, with tests.
  That is a small tax and the right one.
