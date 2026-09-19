# Phase 4 — Hardening

Plain-language version: [phase-4-plain.md](phase-4-plain.md).

**Status:** complete, 2026-09-19, with two acceptance items that cannot be
verified here — see [What could not be verified](#what-could-not-be-verified).

## What this phase is for

Everything so far works when watched. This phase is about what happens when it
is not: a nightly run nobody reads, a failure nobody sees, an API that changes
shape while the pipeline keeps reporting success.

## The failure this phase exists for

The full six-year backfill was launched, ran happily through three quarters of
2020, and then logged:

```
2020-09-27..2020-12-26 -> 0 rows, 0 partitions
```

No error. No failed chunk. Exit code 0. Probing the same window by hand
afterwards returned **30,015 rows for a single batch of 200 satellites**. The
data had been there the whole time.

**Space-Track answers a throttled request with HTTP 200 and an empty JSON
array** — not a 429, not an error. The ingestion had no way to tell "you are
asking too fast" from "there is nothing here", so it recorded a cheerful zero
and moved on, leaving a quarter-sized hole that nothing downstream could
distinguish from a genuinely quiet period.

This is the worst class of bug in a pipeline: it is silent, it is plausible, and
it corrupts a result rather than stopping it.

**The fix.** `GpIngestReport` now records every window that returned nothing,
and `has_suspicious_gap` is true when an empty window sits beside a non-empty
one. The Dagster asset raises on it, which turns a silent hole into a retry.

A window that is *legitimately* empty does not trip it — backfilling from before
the first launch has genuinely empty windows, and failing those would be its own
bug. The signal is the contrast, not the emptiness.

Two tests pin both halves: an empty window beside a full one is a gap; an
entirely empty run is not.

## Schema drift, the other silent failure

If Space-Track renames a field or NASA retires a parameter, nothing crashes. The
cast produces nulls, the contract passes them, and every downstream number is
computed from progressively emptier data while every check stays green.

`starlink-drag check-upstream` compares a live response's field names against
what the parsers actually read:

```
ok    nasa.omni: 55 parameters declared, all 5 we read are present
```

It checks `gp_history`, `satcat` and the OMNI parameter list, reports the
observed fill markers (a changed sentinel is how a fake 999.9 solar flux enters
the science), and **skips cleanly without credentials** so a fork gets a skip
rather than a red build. The nightly workflow runs it.

## Coverage

`science/` is at **100%**, and the floor is enforced rather than observed:

```bash
make test-science     # --cov-fail-under=90
```

The nightly workflow runs that target, so the number cannot quietly drift back
down. Only `science/` is gated: it holds the pure functions every result depends
on, and it is the one package that can be covered exhaustively without a network
or a warehouse.

## dbt against seeded fixtures

`tests/integration/test_dbt_build.py` runs the **real dbt project** — every
model, all 90 tests — against a database built in the test, in about a second.

The fixture is generated rather than captured, using real NORAD IDs drawn from
the committed generation seed. That means the generation join is genuinely
exercised, while no Space-Track data is involved: the orbits are synthetic and
deliberately so.

Six tests, each asserting something a green build alone would not:

| Test | Asserts |
| --- | --- |
| build is green | every model and dbt test passes on the production SQL |
| marts are populated and joined | a green build over empty tables proves nothing |
| decay points downward | rising mean motion must produce a falling altitude |
| the storm is found | one seeded storm, detected and superposed on the right day |
| weather joins onto every day | no null space weather |
| a duplicated bronze row does not reach the marts | the property ADR-0005 trades partition-replacement for |

Volume expectations are now dbt **vars** (`min_satellites`,
`min_daily_decay_rows`) defaulting to production scale and overridden by the
fixture run — so the same tests run against ten rows without weakening the real
thresholds or maintaining a second suite.

## The nightly workflow

Two jobs, because they need different things.

**`regression`** is self-contained: lint, types, the coverage floor, and the
whole suite including the real dbt build. No credentials, no network. It exists
to catch the drift a quiet repository still suffers — a transitive dependency
changing behaviour under the same lockfile constraints, a DuckDB or dbt release
altering a result.

**`upstream_contract`** is the one that needs the network, and runs
`check-upstream`. It is `continue-on-error`, so a transient outage reports
rather than fails.

**Neither ingests into a lake, deliberately.** A GitHub runner has no persistent
storage, so a scheduled ingest only makes sense once the lake is on R2. Writing
a workflow that pretends to keep a warehouse warm would be worse than writing
one that does less.

## Infrastructure

**`infra/terraform/`** provisions the R2 bucket. The lifecycle rules are the
substance: bronze is append-only, so a re-run leaves the previous copy of a
partition behind *permanently* and nothing in the pipeline removes it. Without
`expire-superseded-iceberg-files`, storage grows with every backfill rather than
with the data. A second rule abandons incomplete multipart uploads, which a
killed backfill leaves behind as invisible billed storage.

Access keys are deliberately **not** created in Terraform — that would put them
in state.

**`infra/docker/`** runs MinIO and Dagster locally. MinIO stands in for R2;
both speak S3, so only the endpoint and credentials change between local and
production, which is what `LAKE_BACKEND` exists for.

The daemon is a separate service on purpose: schedules and the concurrency key
live there, not in the webserver. Running it is what closes the Phase 3 gap
where the daily schedule had never fired.

## The runbook

[`docs/runbook.md`](../runbook.md) covers API auth failure, rate-limit
exhaustion, schema drift, partition failure, safe re-running, and four more.
**Every entry is something that actually happened during development** — the
throttling hole, the session expiring mid-backfill, the killed process, the
Windows path limit, the stale-view trap.

It leads with the thing worth knowing first: partitions are idempotent and
bronze appends, so re-running is almost always safe.

## Verification

| Check | Result |
| --- | --- |
| `ruff`, `ruff format`, `mypy --strict` | clean, 55 source files |
| `pytest` | **232 passed**, no network |
| `science/` coverage | **100%**, floor enforced at 90% |
| `dbt build` against seeded fixtures | green, 90 nodes, in about a second |
| `check-upstream --source omni` | ran live: all 5 parameters present |
| `docker compose config` | valid, 4 services |
| Empty-window guard | two tests: gap detected, legitimate emptiness not |

## What could not be verified

**The nightly workflow has never run.** Its acceptance is "runs unattended for a
week without manual fixes", which needs a week and a GitHub remote — and this
repository still has neither. The YAML is valid and every command it invokes is
verified locally, but nobody can yet say it is green. This has been open since
Phase 0.

**Terraform is unvalidated.** `terraform` is not installed on this machine, so
the configuration has not been through `init`, `validate`, `fmt` or `plan`. It
is written against the Cloudflare provider's v5 schema and should be treated as
a first draft until someone runs `terraform plan` against a real account.

**docker-compose was validated but not run.** `docker compose config` accepts it
and resolves all four services, but the Docker daemon is not running on this
machine, so no container was ever started. The image build in particular — a
full `uv sync` inside `python:3.12-slim` — is unproven.

I would rather say that plainly than let three green ticks imply more than was
done.

## A bug found while re-running the backfill

The first full backfill failed immediately with
`Could not find a partition with key 2026-09-18`. The CLI defaulted its end date
to "yesterday" in **local** time; Dagster's daily partitions are **UTC**, and a
partition exists only once its day is complete. At 02:00 IST the newest real
partition was two days behind local yesterday.

It now asks the partition definition for its last key rather than computing one.
That removes the whole class of bug rather than the instance.

## The six-year backfill

Still the honest sticking point. A full 2020-to-present run is **~1,755
requests** — 27 windows of 90 days, 65 NORAD batches each — which against
Space-Track's ceiling of 300 per hour is close to **six hours** of wall clock,
almost all of it waiting.

Two runs have been killed mid-flight by the session ending, which is what the
runbook's "the run died" entry is about. It can be shortened: raising
`norad_ids_per_request` from 200 to 500 would cut it to ~700 requests and about
two and a half hours, and the URL stays well inside any sane length limit. That
is a tuning change I have not made mid-run.

## Next

Phase 5 — serving and documentation: the Streamlit explorer reading gold marts
only, the full README with an architecture diagram above the fold, and published
dbt docs.
