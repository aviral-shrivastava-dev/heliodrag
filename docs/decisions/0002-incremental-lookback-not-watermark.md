# 2. Incremental loading with a lookback window, not a watermark

Date: 2026-09-18 · Status: Accepted

## Context

`int_satellite_daily_state` aggregates every bronze partition. Rebuilding it on
each run re-reads the entire history, which does not scale as the backfill grows.

The obvious incremental filter is a watermark — select only dates greater than
the maximum already present in the table.

## Decision

Incremental with `delete+insert` over a trailing lookback window, defaulting to
seven days and configurable via the `gp_lookback_days` variable.

## Rationale

Space-Track republishes *revised* element sets for epochs it has already issued.
A day loaded last week can legitimately change when a better orbit solution is
computed.

Under a strict watermark that revision is never seen. Nothing errors, no test
fails, and the warehouse quietly keeps the superseded value — the worst class of
data bug, because it is silent. Re-processing a trailing window and replacing
those days picks the correction up.

Seven days comfortably exceeds the observed revision lag while keeping the
incremental scan small.

## Consequences

- Each run reprocesses roughly seven days rather than one. Still a large saving
  over a full rebuild, and the cost is bounded.
- The model must be idempotent, since days are reprocessed. Verified: a second
  run leaves zero duplicate satellite-day keys.
- Revisions older than the lookback are still missed. A periodic full refresh is
  the backstop.
