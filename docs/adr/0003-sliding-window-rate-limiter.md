# ADR-0003: A sliding-window rate limiter, not a token bucket

- **Status:** accepted
- **Date:** 2026-09-18

## Context

Space-Track.org publishes two simultaneous limits: fewer than 30 requests per
minute *and* fewer than 300 per hour. These are enforced by blocking the
account, not by returning a retryable error. A block during a multi-hour
backfill costs the run; a repeated block costs the account, and with it six
years of accumulated history that would take many hours to re-acquire under the
same limit.

The build brief specifies a token-bucket limiter. A token bucket sized to the
limit — capacity 29, refilling at 29 tokens per 60 seconds — does not satisfy a
**rolling**-window limit:

```
t=0s    bucket full (29 tokens), 29 requests fire, bucket empty
t=60s   bucket has refilled to 29, 29 more requests fire
```

The window `[1s, 61s]` now contains 58 requests against a published limit of 29.
This is not a pathological case; it is what a token bucket does whenever demand
exceeds supply, which is precisely the situation during a backfill.

The general result is that a token bucket with capacity `C` refilling at
`C`-per-window admits up to `2C` inside one rolling window. Reducing the
capacity to 1 removes the burst and is provably safe, but also removes any
burst: with the hourly limit binding, that is one request every 12 seconds even
when the account has been idle for an hour.

## Decision

Implement `SlidingWindowRateLimiter`, which records the start time of recent
requests and holds a new request until the oldest has aged out of every
configured window.

- Several windows are enforced at once. The real configuration is
  `[(29, 60s), (299, 3600s)]`, one below each published limit.
- It cannot exceed a rolling limit by construction: a request starts only when
  strictly fewer than `limit` starts fall within the last `window` seconds.
- A burst is still allowed when the window genuinely is clear, so a 40-request
  job runs at full speed rather than being throttled to the sustained rate.
- The clock and sleep function are injected, so the property is tested against
  hundreds of simulated requests in milliseconds rather than being asserted
  about sleep calls.

The limits themselves are validated configuration, capped at 29 and 299 in
`SpaceTrackSettings`. They can be lowered — tests do — but not raised. A
misconfiguration fails at startup instead of getting the account blocked at
runtime.

## Alternatives considered

**Token bucket with capacity equal to the limit**, as the brief words it.
Rejected: it knowingly admits up to twice the published rate at a window
boundary. Shipping a limiter whose failure mode is the exact outcome it exists
to prevent is not a defensible trade.

**Token bucket with capacity 1.** Literally a token bucket and provably safe,
because no burst is possible. Rejected on throughput: every request waits the
full sustained interval even when the account has been idle, which turns short
interactive jobs into multi-minute ones for no safety gain over the sliding
window.

**Reacting to HTTP 429.** Necessary anyway and implemented — `Retry-After` is
honoured, and retryable statuses get exponential backoff with full jitter — but
it is not a substitute. Space-Track blocks rather than throttles, so the first
signal that the limit was exceeded can be the account being unusable.

## Consequences

- The class name does not match the brief's wording. This record is the
  explanation, and the module docstring points at it.
- Memory is bounded by the largest window's limit — a few hundred floats.
- The limiter is per-process. A second process running concurrently would have
  its own window and the pair could exceed the limit together. Acceptable now,
  because ingestion is single-process; Phase 3 must not introduce parallel
  Space-Track assets without moving this state somewhere shared. That is a real
  constraint on the orchestration design and is recorded here so it is not
  discovered by being blocked.
- Full jitter on backoff means retry timing is not reproducible. That is the
  point — deterministic backoff resynchronises failed batches into lockstep —
  but it does mean retry behaviour cannot be asserted exactly, so tests inject a
  fixed jitter function.
