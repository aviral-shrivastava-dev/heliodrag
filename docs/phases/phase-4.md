# Phase 4 — Hardening

Plain-language version: [phase-4-plain.md](phase-4-plain.md).

**Status:** complete, 2026-09-19. What could not be verified then was verified
on 2026-09-25, once the repository was on GitHub and Terraform and Docker were
installed — see [Verified after the fact](#verified-after-the-fact). Doing so
found nine more defects. Two things remain unverified — see
[Still not verified](#still-not-verified).

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

**Corrected after the fact.** As first built, the Space-Track check probed one
hard-coded satellite, STARLINK-1007 (NORAD 44713) — which had re-entered on
2024-10-02, two years before the check was written. It went unnoticed because
the repository had no Space-Track secrets, so every nightly run skipped the
check. The first run with secrets reported `DRIFT: no elements for 44713`. It
now picks five satellites from the live catalogue each run (the newest still on
orbit and at least a month old), and reports an empty answer as `EMPTY`, not
`DRIFT`, because an empty 200 is also what throttling looks like.

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
live there, not in the webserver. Running it is what would close the Phase 3 gap
where the daily schedule had never fired. It has been started, but a schedule
tick has not yet been observed — see [Still not verified](#still-not-verified).

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

## Verified after the fact

When this phase was built, three things could not be verified on this machine:
the nightly workflow (no GitHub remote), Terraform (not installed) and
docker-compose (no Docker daemon). The first version of this document said so.
All three have since been run, and running them found nine defects that no
syntax check could.

**The nightly workflow ran unattended for six days.** The repository went to
GitHub on 2026-09-19. CI failed twice first, on two assumptions that held only
on this machine: the dbt manifest and the dbt packages both existed locally and
nowhere else. Once those were fixed, every scheduled nightly run from 2026-09-20
to 2026-09-25 passed — six in a row, on one unchanged commit, with no
intervention. They start around 09:00 UTC rather than the 04:00 in the cron,
because GitHub delays scheduled workflows under load.

That record covered less than it appeared to. **The Space-Track contract check
was skipping every night**, because the repository had no Space-Track secrets
and the check is built to skip rather than fail without them. When the secrets
were added on 2026-09-25, its first real run failed:

```
DRIFT  spacetrack: no elements for 44713 in the last 7 days
```

That was not drift. The check probed one hard-coded satellite, STARLINK-1007,
which re-entered on 2024-10-02 — so it could never have passed. It now picks
five live satellites from the catalogue on every run (see
[Schema drift](#schema-drift-the-other-silent-failure)), and its next run
passed:

```
ok    spacetrack: 72 element sets from 5 satellites, all 33 gp fields and 18 satcat fields present
```

The lesson generalises: a check that skips cleanly is indistinguishable from
one that passes, unless someone reads the log.

**Terraform validates.** `terraform validate` against the real Cloudflare v5
provider schema rejected the lifecycle rules twice over. Every rule requires a
`conditions` block with a prefix, and the abort-uploads rule had none. And the
age attribute is `max_age`, not the camelCase `maxAge` of R2's REST API — found
by reading the provider schema, since `validate` stops at the first error. Both
are fixed; `fmt`, `init` and `validate` pass. The committed
`.terraform.lock.hcl` records provider checksums for Linux, macOS and Windows on
amd64 and arm64, because `init` records only the platform it runs on.

**docker-compose runs.** Starting it found four defects:

- MinIO no longer publishes `minio/minio` on Docker Hub; both images now come
  from `quay.io`, MinIO's own registry.
- The image build failed with `License file does not exist: LICENSE`: hatchling
  needs the file `pyproject.toml` declares, and the Dockerfile did not copy it.
- The webserver would not start. `dagster-webserver` was in the `dev` group and
  the image installs `--no-dev`, so it moved to a `serve` group that `dev`
  includes and the image installs explicitly.
- There was no `.dockerignore`, so every build sent `data/` — 5.8 GB of
  Space-Track data that must not be redistributed — to the Docker daemon.

After the fixes: MinIO healthy, bucket created, image built, webserver answering
HTTP 200 on port 3000, daemon up.

| Check | Result |
| --- | --- |
| Scheduled nightly runs | **6 of 6 green**, 2026-09-20 to 2026-09-25, unattended |
| `check-upstream --source spacetrack` | skipped until 2026-09-25; failed on a dead probe; fixed; **passes** |
| `terraform fmt`, `init`, `validate` | **pass**, after two schema fixes |
| `docker compose up` | **runs**, after four fixes |
| `pytest` | **239 passed**, including the tests for the probe fix |

## Still not verified

**`terraform plan` and `apply`** need a Cloudflare account and API token, so
the configuration has been checked against the provider's schema but never
against a real account.

**The Dagster daemon has not been seen firing a schedule.** It starts and stays
up, but no scheduled tick was observed, and its log showed a code-server
warning (`No heartbeat received in 20 seconds, shutting down`) that has not been
investigated.

*Update, 2026-09-26:* the stack was in worse shape than this section says. The
image had no dbt manifest, so the project never loaded in either container --
most likely the cause of that warning -- and the pipeline could not write to
MinIO at all. Both are fixed
([ADR-0010](../adr/0010-one-lake-module-for-every-storage-backend.md)): the
project loads, a two-day backfill runs inside the stack against MinIO, and every
Dagster daemon, the scheduler included, reports healthy. A scheduled tick has
still not been watched fire.

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
