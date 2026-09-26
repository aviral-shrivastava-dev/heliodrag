# ADR-0008: The rate limit is shared across processes, and the element fetch resumes

- **Status:** accepted
- **Date:** 2026-09-26
- **Supersedes:** the part of [ADR-0003](0003-sliding-window-rate-limiter.md)
  that kept the limiter's history in process memory. The sliding-window
  algorithm itself still stands.

## Context

On 2026-09-26 the backfill of 2025 onwards broke Space-Track's hourly limit.
Three things combined:

1. **Memory.** The element fetch held a whole 90-day window before writing it.
   At 2025 volumes that is about 1.7M element sets, and the write ran a 16 GB
   machine out of memory.
2. **A per-process limiter.** Dagster retried the failed step as a new process.
   The limiter's history lived in the old process's memory, so the retry
   started counting from zero while the failed attempt's requests were still
   inside Space-Track's rolling hour.
3. **Retrying from the start.** With `BackfillPolicy.single_run`, the retry
   re-requested every window of the range, including the ones already landed.

About 350 requests went out in half an hour, against a limit of 300 an hour,
and Space-Track answered the excess with empty responses. A fourth problem
surfaced alongside: after the crash, dlt's next run silently lost one of two
batches -- the leftover from the crash, or the new data.

ADR-0003 had named the per-process limit as a known gap and covered it with an
operational rule and a Dagster concurrency key. Neither covers a retry, which
is the same asset running again.

## Decision

- **One ledger per machine.** Request start times go in a SQLite file,
  `data/.spacetrack/ledger.sqlite`. Each decision -- read the last hour, decide,
  record -- happens inside `BEGIN IMMEDIATE`, SQLite's write lock, so two
  processes can never both see room for the same request. The limiter's clock
  becomes wall time, because a monotonic clock means nothing outside the
  process that read it.
- **Headroom for what the ledger cannot see.** The limit belongs to the
  account, and the nightly contract check on GitHub uses it from another
  machine. Defaults drop from 29 a minute and 299 an hour to 25 and 290.
- **Writes bounded by rows.** The fetch writes whenever it has collected
  400,000 rows, whatever the window. The request count is unchanged.
- **Retries resume.** Each Dagster run records the windows it has landed in a
  checkpoint file keyed by run id; a retry -- same run, new process -- skips
  them. Windows with a failed request, or that came back empty, are not
  recorded, so a retry asks again. Space-Track steps retry after 10, 20 and 40
  minutes rather than 30 seconds.
- **Crash leftovers are finished, never traded.** Before any bronze write,
  pending dlt packages are normalised and loaded; if anything is still pending,
  the write refuses.

## Alternatives considered

- **Keep the limiter in memory and turn off step retries.** Removes one route
  to the problem and leaves the others: a second command, a second backfill,
  the catalogue and element steps running back to back. And it throws away the
  retry-and-recover behaviour Phase 3 was accepted on.
- **A lock file holding a list of timestamps.** Cross-platform file locking is
  exactly what SQLite already does correctly; writing it again is risk for no
  gain. SQLite is in the standard library, so it adds no dependency.
- **A shared service or Redis.** A second thing to run for a problem one file
  solves.
- **Shorter windows instead of a row budget.** Thirty-day windows would bound
  memory too, but triple the request count, because requests scale with the
  number of windows. The budget bounds memory at no cost in requests.
- **Discard crash leftovers instead of loading them.** Also safe for a retried
  window, but it would silently lose the last window of a one-off command that
  is never re-run. Loading is append-only and deduplicated downstream.

## Consequences

- The limit holds however many processes run, on this machine. It does not
  hold across machines; the lower defaults are the only guard there, and the
  runbook says to split the budget if ingestion ever runs in two places.
- Two ingestions at once no longer break the limit, but share it, so each
  runs slower.
- A large window is written in several pieces, so its partitions get a few
  files each rather than one. Bronze already appends; the file counts grow,
  the bytes do not.
- The ledger and checkpoints are local state under `data/`. Deleting the
  ledger forgets the last hour: don't, while anything is running.
- A manually re-executed Dagster run is a new run, with a new checkpoint, and
  re-fetches from the start of its range. Safe under the shared limit; slower.
