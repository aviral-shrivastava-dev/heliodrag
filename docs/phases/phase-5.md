# Phase 5 — Serving and documentation

Plain-language version: [phase-5-plain.md](phase-5-plain.md).

**Status:** complete, 2026-09-26. The data behind it runs to 2025-12-26; the
rest waits on ingestion fixes described under
[What is genuinely unresolved](#what-is-genuinely-unresolved).

## What this phase is for

Four phases built a pipeline that only its author could run and only a data
engineer could read. This phase makes it usable by someone else: an explorer
that shows what the marts contain, a README that explains the project and gets
a stranger from clone to running explorer, and published docs for the models.

The acceptance criterion was that **someone who has never seen the repository
can run it locally using only the README**. That was tested literally: a clean
copy of the repository -- exactly the files a clone would contain, no `data/`,
no `.venv`, no dbt artefacts -- was run with the README's command. See
[How to check it works](#how-to-check-it-works).

## What was built

| Path | What it does |
| --- | --- |
| `app/streamlit_app.py` | The explorer. Layout only: filters, four tabs, captions |
| `src/starlink_drag/serving/marts.py` | Every query the explorer runs, against gold marts only |
| `src/starlink_drag/serving/charts.py` | Every chart, as pure functions from frames to Altair specs |
| `transform/models/marts/fct_space_weather_daily.sql` | New gold mart: one row per day of space weather |
| `transform/tests/assert_dim_satellite_is_the_latest_catalogue.sql` | Guard against a DuckDB bug found this phase |
| `src/starlink_drag/launch.py` | `starlink-drag demo` and `starlink-drag app` |
| `src/starlink_drag/docs_site.py`, `dbt_runner.py` | `starlink-drag docs-site`: the dbt docs as one page, built from an empty warehouse |
| `src/starlink_drag/cli_serving.py` | The new commands, registered on the CLI |
| `.github/workflows/docs.yml` | Builds the docs page and deploys it to GitHub Pages |
| `.streamlit/config.toml` | Headless mode, no usage statistics, viewer toolbar |
| `README.md` | Rewritten: summary, diagram, question, setup, output, stack, cost, trade-offs |
| `docs/architecture.md`, `.mmd` | The architecture, layer by layer, with the diagram's single source |
| `docs/adr/0006`, `0007` | Serving reads gold through short-lived connections; publish schema and aggregates only |
| `tests/integration/fixture_warehouse.py`, `conftest.py` | The synthetic warehouse, shared by the dbt, serving and app tests |
| `tests/unit/test_serving_marts.py`, `test_charts.py`, `test_launch.py` | Gold-only rule, chart rules, the one-command path |
| `tests/unit/test_readme.py` | The README's diagram, links and command stay true |
| `tests/integration/test_serving.py`, `test_docs_site.py` | Queries and the whole app against a real dbt build; the docs page |

## Why it was built that way

### The explorer reads gold, and never holds the file

Gold is the contract; silver changes whenever a cleaning rule does. A unit test
scans every query for the tables it reads, and another checks the list against
the models dbt tags `gold`. When the explorer needed daily space weather, which
existed only in silver, gold grew a mart for it with its own tests.

DuckDB allows one writer, and a reader holding the file blocks it. So every
query opens the file read-only, runs and closes; results are cached for ten
minutes, connections never are. See
[ADR-0006](../adr/0006-explorer-reads-gold-through-short-lived-connections.md).

### Chart decisions, and the data that forced two of them

- **No second y-axis anywhere.** Decay and space weather have different units.
  Space weather is a strip of its own on the same time axis, and storm days
  (Dst at or below -50 nT) are shaded down through every panel, so the eye
  compares by position.
- **Colour follows the generation.** Six fixed slots from a palette checked
  with a colour-blindness validator in both light and dark mode (adjacent CVD
  separation >= 8.4, normal-vision >= 19.3). Three light-mode colours sit below
  3:1 contrast, so every panel is titled and every chart has a data table.
- **Smoothing, on by default, explained on the page.** Real data showed the raw
  daily median zigzagging: for v1.0 in its operating shells, consecutive days
  have a lag-one autocorrelation of **-0.69**. Each daily rate is the difference
  of two orbit fits, so one fit's error lands on two neighbouring days with
  opposite signs. A centred seven-day mean shows the trend without moving a
  storm later in time, and treats a missing day as a gap.
- **A shared scale from the 1st to 99th percentile.** On the first real render
  a handful of days -- satellites in commanded descent below 525 km, falling at
  up to 830 m/day -- stretched every panel to -600 m/day and flattened v1.5 to a
  line. The shared scale now follows the bulk of daily medians and clips beyond
  it, and says so in the caption.
- **Storm response relative to the pre-storm level.** Superposing storms and
  subtracting each storm's own five-day pre-peak mean removes each generation's
  background -- station-keeping, commanded descent -- and leaves the response.
  On 2024 alone, every generation drops at the peak.

### One command, and it is not `make`

The developer's own Windows machine has no `make`; the README's previous
`make setup` would have failed there. The one command is therefore
`uv run starlink-drag demo`, which needs only uv. It checks for credentials
(copying `.env.example` to `.env` if there is none, and saying exactly what to
fill in), installs dbt packages and writes the manifest Dagster needs, runs the
**production** backfill over the most recent 30 days, and starts the explorer.
`.streamlit/config.toml` turns on headless mode: otherwise the first
`streamlit run` on a machine stops to ask for an email address.

No data ships with the repository, and none is synthesised for the demo
([ADR-0007](../adr/0007-publish-schema-and-aggregates-never-element-data.md)).

### dbt docs from a warehouse with no rows

The catalog is read from a database, and the real one may not be published.
`docs-site` creates bronze tables with the production schema and no rows, loads
the CC-BY generation seed, runs the models with `--empty`, and generates the
static page from that. It has every model, column type and lineage edge, and a
test checks the database it came from is empty. The temporary database is named
`atlas.duckdb` so relation names match production.

### The diagram has one source

`docs/architecture.mmd` is the diagram. The README and `architecture.md` embed
it, and a test fails if either copy differs.

## What running it on real data found

**DuckDB 1.5.5 built `dim_satellite` from one file in twenty.** The first build
over multi-year data failed two tests: `dim_satellite` held 865 satellites, and
5,353,341 rows of `fct_daily_decay` had no matching satellite. `dim_generation`,
built a second earlier from the same model, had all 12,892. The 865 were exactly
the first Parquet file of the twenty behind `bronze.satcat`. Bisecting on a copy
of the warehouse: a latest-snapshot filter (join or scalar subquery) together
with `QUALIFY row_number() ... = 1` loses rows **inside `CREATE TABLE AS`** --
how dbt builds every table -- while the same `SELECT` is correct. It reproduced
30 times out of 30 on the real lake and not at all on a small synthetic one.
Every other mart was checked by comparing its stored rows with a fresh query of
its compiled SQL; only `dim_satellite` was affected. The model now finds the
latest snapshot with a window, which does not trigger it, and a singular test
compares the built table with the catalogue on every build. 1.5.5 is the latest
stable release, so there was no upgrade to take.

**The rate limiter is per process, and a backfill is several processes.** Found
before it did harm: a background resume was queued to start its second range
the moment the first finished, which could have put about 450 requests into one
hour. It was stopped, and restarted an hour later with the hourly budget
lowered to 280. The runbook now says why.

**An empty catalogue response had the wrong types.** `satcat.to_frame([])`
returned every column as text, where a full response returns integers and
dates. An empty response is what a throttled Space-Track sends. It now takes
its types from the contract, with a test.

**Space-Track published half as often in the summer of 2023.** From late June
to mid-August 2023 there are about 1.3 element sets per satellite per day
instead of about 2.5, and on 2023-07-22 there are five in total. Throttling is
ruled out: it empties whole 200-satellite, 90-day batches, whereas here about
4,000 satellites still appear every day. It is a property of the upstream data,
and fewer intervals in that period pass the two-day cleanliness rule.

**The backfill ran out of memory, and its retry broke the rate limit.** The
last range, 2025 onwards, failed on its fourth window with `ArrowMemoryError`:
the element fetch holds a whole window before writing, and a 90-day window of
2025's constellation is about 1.7M element sets. Dagster's retry policy then
restarted the step as a new process, with a new rate limiter, from the start of
the range: about 350 requests in half an hour against a limit of 300 an hour.
Space-Track answered with empty windows, and the run was stopped by hand. This
breached a hard constraint of the project. It is recorded here and in the
runbook rather than smoothed over, and no further Space-Track request has been
made since.

The same retry showed a third problem: after a crash, dlt's next write loads
the leftover batch **instead of** the data it was given, while the asset logs
the new row count as written. Nothing was lost this time -- the swallowed window
had been landed by the first attempt -- but in general it is silent data loss.

The lake holds everything up to 2025-12-26, and the warehouse was rebuilt from
it offline: 97 of 98 dbt nodes pass (one warning: six bad preliminary fits
above 2,000 km, which the analysis-ready flag already excludes), every mart
matches a fresh query of its SQL, and `dim_satellite` has all 12,892
satellites.

## How to check it works

```bash
uv run pytest                                   # includes the app, run headless
uv run starlink-drag docs-site --output site    # then open site/index.html
uv run starlink-drag demo                       # the acceptance path
```

| Check | Result |
| --- | --- |
| Clean copy of the repository, `uv run starlink-drag demo` | **passed**: explorer serving 7 min 12 s after the command, including installing 186 packages; 951,965 element sets for 30 days in 56 requests; `dbt build` 98 of 98; 10,738 satellites and 303,698 satellite-days on screen |
| `ruff`, `ruff format`, `mypy --strict` | clean, 71 source files, `app/` now included |
| `pytest` | **303 passed** (239 before this phase), no network |
| The whole explorer under Streamlit's `AppTest` | every tab renders; raw and smoothed; no-warehouse message |
| `docs-site` | one 3.4 MB page with every model and column type, in about a minute |
| `dbt build`, fixed model, on a copy of the 2020-2024 warehouse | 58 of 58 downstream nodes pass, including the new guard |
| Upstream contract check, nightly on GitHub | `ok spacetrack: 72 element sets from 5 satellites` |

## What is deliberately missing

- **A hosted explorer.** It would redistribute Space-Track data. Local only.
- **Sample data.** Same reason; synthesising some is against the project's rules.
- **The statistics.** The explorer shows the data and does not test the
  hypothesis. Phase 7 does: regression with bootstrap confidence intervals.
- **An aggregates-only public explorer.** Permitted and worthwhile, but not
  needed to run the project.

## What is genuinely unresolved

- **The ingestion fixes the backfill exposed are not made yet**, because they
  belong to the ingestion layer rather than to this phase and should be
  reviewed on their own: a rate limiter shared between processes, bounded memory
  in the element fetch, no automatic retry that re-requests landed windows, and
  explicit handling of batches dlt leaves behind after a crash. Until then, the
  runbook's workaround applies, and 2025-12-27 onwards is not landed.

- **Why DuckDB drops the rows.** The workaround and the guard are in place, but
  a shareable reproduction -- the only kind DuckDB's maintainers can use -- has
  not been found, because the triggering lake cannot be shared.
- **GitHub Pages needs one setting.** The workflow builds the page on every
  change to the models; publishing waits on Settings -> Pages -> Source: GitHub
  Actions, which only the repository owner can set.
- **The demo was run end to end on Windows only.** Linux runs the full test
  suite in CI, including the app under `AppTest`, but not the live demo.
- **A shared rate limiter** across processes would replace an operational rule
  with a guarantee.
