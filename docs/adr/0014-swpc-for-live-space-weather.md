# ADR-0014: The live path takes space weather from NOAA SWPC, not OMNI

- **Status:** accepted
- **Date:** 2026-09-26

## Context

Phase 6's brief names OMNI as the space-weather input to the live nowcast. OMNI
is the batch path's source for good reason -- it is the merged, quality-controlled
record -- but it is assembled after the fact. On 2026-09-26 HAPI's newest OMNI
hour was 2026-09-17 (runbook: status 1201). A nowcast built on it would show
space weather a week old beside element sets a few hours old.

## Decision

The streaming path reads NOAA's Space Weather Prediction Center, chosen by the
project owner over OMNI and over using both:

| Quantity | SWPC product | Cadence |
| --- | --- | --- |
| Kp, estimated | `json/planetary_k_index_1m.json` | 1 minute, provisional |
| Kp and ap | `products/noaa-planetary-k-index.json` | 3 hours |
| Dst | `products/kyoto-dst.json` (Kyoto quick-look) | 1 hour |
| F10.7 | `products/10cm-flux-30-day.json` | daily, 20:00 UTC |

Public, no account, public domain. Polled every 5 minutes by default, with a
floor of 1: SWPC does not publish a rate limit, and its fastest product updates
once a minute.

Each value is checked against what the quantity can physically be (Kp 0-9, ap
0-400, Dst -2,000 to +500 nT, F10.7 30-900 sfu) and a value outside is sent to
the rejected topic with its reason. Time tags, which SWPC publishes without a
zone, are made explicitly UTC before they leave the client.

**The batch path is unchanged: OMNI remains its only space-weather source.**
Nothing in the marts, and nothing the Phase 7 analysis will read, comes from
SWPC.

## Alternatives considered

- **OMNI, as the brief says.** No new source, but a week late: the nowcast
  would not be a nowcast.
- **Both** -- SWPC live, OMNI replacing it as it arrives. Correct in principle,
  but it makes the nowcast table a mix of provisional and final values that a
  reader cannot tell apart, for a page whose purpose is "now".

## Consequences

- The live and batch paths can disagree about the same hour. SWPC's quick-look
  Dst and estimated Kp are revised; OMNI carries the final values. The live
  page says its inputs are provisional.
- A new upstream means a new way to break. The client is tested against
  responses captured on 2026-09-26 (`tests/fixtures/swpc/`), and a changed
  shape arrives as rejected records rather than as a crash. It is not yet in
  `check-upstream`'s nightly contract check.
