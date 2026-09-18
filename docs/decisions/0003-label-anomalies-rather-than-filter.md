# 3. Label anomalous records rather than filter them

Date: 2026-09-18 · Status: Accepted

## Context

Two range tests failed the first time models were built against real data: 16
rows outside the plausible semi-major-axis range and 10 outside the plausible
decay range.

Investigation showed both were real. NORAD 64297-64316 — consecutive IDs from one
launch — appear on two days at 1,138-2,379 km, then three days later at 334 km:
orbit determination settling after deployment, not motion. Separately, satellites
at 223-238 km losing ~50 km/day are genuinely reentering.

## Decision

Add `orbit_regime` (`operational` / `reentering` / `unconverged_or_transfer`) and
`is_implausible_decay`. Scope the range tests to the operational regime. Keep
every row.

## Rationale

The tempting fix is widening the thresholds until the tests pass, which destroys
their value: a test that accepts anything detects nothing.

Deleting the rows is also wrong. Reentering satellites are the physically
cleanest drag measurement available, because they are no longer station-keeping —
discarding them would throw away the best data in the set. And silently dropping
post-deployment records would bias any per-satellite statistic toward satellites
that settled quickly.

Labelling keeps the tests strict where strictness means something, keeps the
anomalies countable, and lets each downstream model state its own requirements.

## Consequences

- Consumers must filter on `orbit_regime` deliberately. Documented per model.
- The regime boundaries (700 km, 200 km) are judgement calls, stated in the SQL.
