# Phase 6 — Streaming: a live, per-generation drag nowcast

Plain-language version: [phase-6-plain.md](phase-6-plain.md).

**Status:** complete, 2026-09-26. Optional; built on the owner's explicit
confirmation.

## What this phase is for

The batch path answers the research question from daily data, a day or more
after the fact. This phase adds the other half of a data platform: a streaming
path that shows, within minutes, how hard the atmosphere is pulling on each
Starlink generation right now.

The brief's acceptance had two halves: **the nowcast updates live**, and **the
batch tests still pass untouched**. The second shaped the design more than the
first. The research question does not need this path; it is scoped so that
removing it would leave the batch pipeline exactly as it was.

## What was built

| Path | What it does |
| --- | --- |
| `src/starlink_drag/clients/swpc.py` | NOAA SWPC client: four products, parsing separate from fetching, physical bounds, explicit UTC |
| `src/starlink_drag/stream/producer.py` | Polls Space-Track (hourly floor) and SWPC (5 min); validates; publishes |
| `src/starlink_drag/stream/messages.py` | Message keys and JSON, with every timestamp's offset written out |
| `src/starlink_drag/stream/topics.py` | Creates the three topics with their retention |
| `src/starlink_drag/stream/nowcast.py` | The nowcast state and computation. Pure: no Kafka, no I/O |
| `src/starlink_drag/stream/consumer.py` | Replays the topic, feeds the state, writes snapshots |
| `src/starlink_drag/stream/table.py` | The nowcast's own Iceberg table, `stream/stream_drag_nowcast` |
| `src/starlink_drag/cli_stream.py` | `starlink-drag stream topics / produce / consume` |
| `src/starlink_drag/serving/live.py`, `app/pages/live_nowcast.py` | The explorer's *live nowcast* page, redrawn every minute |
| `infra/docker/Dockerfile.stream`, `docker-compose.yml` | One streaming image; Redpanda, producer, consumer and explorer under the `streaming` profile |
| `src/starlink_drag/config.py` | `StreamSettings` (`STREAM_*`), with floors on both poll intervals |
| `.github/workflows/ci.yml` | A `stream` job: the broker tests against a real Redpanda |
| ADR-0013, ADR-0014 | Redpanda and the topic-as-state design; SWPC as the live forcing |

Topics: `starlink.gp` (keyed by NORAD ID, 7 days), `spaceweather.swpc` (keyed
by quantity, 7 days), `starlink.rejected` (30 days).

## Why it was built that way

### The topic is the consumer's only state

The consumer commits no offsets and keeps no checkpoint. On start it rewinds
to the oldest retained message and replays; the computation ignores repeats
and element sets older than one it holds, so the replay reaches the same state
the live run had. A restart is the recovery procedure, and there is nothing to
corrupt. Seven days of retention bound the replay, which takes seconds.

### Event time, not the clock

The window is the 24 hours of element-set epochs up to the newest epoch seen.
A replay then computes what the live run computed, and a stalled feed shows up
as a stale "newest element set" on the page rather than as a nowcast that
quietly empties as the clock moves on.

### Two measures, on two timescales

**BSTAR**, Space-Track's fitted drag term, is on every element set, so the
nowcast has a drag measure for every satellite from the first message. It is
fitted, so it also absorbs orbit-model error. **Altitude rate** is what
`fct_daily_decay` carries: the orbit change between two element sets of one
satellite. Each rate is measured against the newest earlier element set that
is 6 hours to 3 days older. Space-Track sometimes publishes two element sets
within an hour, and differencing those measures fitting noise. The manoeuvre
threshold is the batch path's, and a test reads the SQL to keep them equal.
Each satellite counts once per generation, with its newest value.

### Validation before publication

Element sets pass the same Pandera contract as bronze; a failure goes to
`starlink.rejected` with its reason, as batch quarantine does. SWPC values are
checked against what each quantity can physically be. A field *missing* from
an SWPC record is a rejection, while an explicit `null` is skipped. That rule
came from reviewing this phase's own ADR: as first written, a renamed field
would have produced no values and no error, which is the silent schema drift
this project has been bitten by before.

### Isolation, enforced

- The Kafka client is in a `stream` dependency group the batch image does not
  install, and the stream commands import it lazily. A test loads the Dagster
  definitions and the CLI in a fresh interpreter and fails if any streaming
  module or the Kafka client is loaded.
- The services carry a Compose profile. The resolved batch configuration of
  `docker compose config` was diffed against the previous commit's: identical.
- The nowcast lives in a `stream` dataset; no warehouse view covers it.
- The batch image's Dockerfile and the explorer's main script are unchanged.
  The live page is a file in `app/pages/`, which Streamlit picks up on its own.

### SWPC, not OMNI

The brief named OMNI. OMNI's newest hour on 2026-09-26 was 2026-09-17, so the
owner chose NOAA SWPC for the live path (ADR-0014). OMNI stays the batch
path's only space-weather source; nothing in the marts comes from SWPC.

## What running it found

- **A time axis drawn in the viewer's time zone.** Vega-Lite renders times in
  the browser's zone by default: the first chart labelled 18:20 UTC as "23:50"
  under an axis titled UTC, in India. The axis now uses a UTC scale, and a test
  pins it. It is the same trap staging fell into earlier the same day (see the
  runbook, "Timestamps are UTC instants"), one layer further out.
- **Windows' path limit, again.** The first consumer run from a deep scratch
  directory failed inside dlt with `[Errno 2] No such file or directory` on a
  260-character path. Nothing in the code changed; runs use short paths.
- **Tiny numbers.** BSTAR is of order 1e-5 to 1e-3, and both the table's and
  Altair's default formats rounded it to nothing. Both are now scientific.

## How to check it works

Unit and page tests run with the rest of the suite; the broker tests need
Redpanda:

```bash
docker run -d -p 19092:19092 redpandadata/redpanda:v26.2.3 redpanda start --mode dev-container --smp 1 --kafka-addr 0.0.0.0:19092 --advertise-kafka-addr localhost:19092
KAFKA_TEST_BOOTSTRAP=localhost:19092 uv run pytest tests/integration/test_stream.py
```

Observed on 2026-09-26:

- **Full suite: 384 passed, 0 skipped** (the 337 existing tests plus 47 new),
  with SeaweedFS and Redpanda both available. No existing test file was edited.
- **The acceptance test**, `test_the_nowcast_updates_while_the_consumer_runs`:
  one consumer running in the background; a first round of element sets gives
  a nowcast with BSTAR and no rates; a second round, 12 hours of epoch later,
  changes the same running consumer's nowcast to rates for all three
  satellites, with no restart.
- **Replay:** two consumer runs over the same topic write identical nowcasts.
- **Live, in Docker** (`--profile streaming up`): the producer's first
  Space-Track request returned the latest element set of all 10,836 on-orbit
  Starlink payloads, none rejected; SWPC gave 679 values, then 25-28 new ones
  every five minutes. The consumer wrote a nowcast of 6 generations and 10,541
  satellites in the window, and the explorer's live page showed it with Kp
  observed two minutes earlier. The streaming containers use about 750 MB.
- **Live rates, no restart:** the producer's second hourly poll (19:20 UTC)
  published 7,282 new element sets; the running consumer's next snapshot moved
  its newest epoch from 14:00 to 16:00 UTC and gave altitude rates for 1,546
  satellites, where the one before had none.

What those first rates say is itself a finding. Rates from element sets hours
apart are noisy -- in v1.5, v2-mini and v2-mini-opt the middle half of the
satellites spans 37 to 90 m/day, within about -40 to +50 -- and those
generations' medians are slightly *positive* (v1.5
+4, v2-mini +17, v2-mini-opt +8 m/day): station-keeping and fit noise, not
drag. Only v2-mini DTC, the lowest flyer, clearly falls (-71 m/day). The
nowcast shows what is happening; it does not remove the confounders the batch
analysis must.

## What is deliberately missing

- **Research use.** Nothing in Phase 7 reads the nowcast or SWPC.
- **Shell-matched comparison.** v2-mini DTC flies far lower than the others,
  in denser air. The page says so; unlike the batch explorer, it does not
  condition on altitude shell.
- **SWPC in the nightly contract check**, and **compaction of the nowcast
  table**, which gains one small Iceberg commit per snapshot. Both are future
  work in the README.
- **A hosted deployment.** Everything runs on the machine you run it on.

## What is genuinely unresolved

- **Short-interval rates are noisy.** A day's worth of element sets per
  satellite gives rates with an interquartile range of up to 90 m/day in the
  working generations. BSTAR is
  the steadier live signal; whether a longer minimum interval, or a
  per-satellite fit over the window, would make the rate usable live is open.

- **Cold-start latency of the rates.** With an empty topic, altitude rates need
  a satellite's second element set at least six hours after its first, and the
  producer polls hourly. The first rates arrive with the first poll after that.
- **The container's rate-limit ledger** counts only the container's requests.
  Two an hour leaves wide headroom, but it is not the shared, machine-wide
  guarantee the batch path has (ADR-0008).
- **Redpanda's licence.** Community edition is source-available (BSL), not open
  source. Any Kafka-compatible broker can replace it without code changes.
- **The navigation label.** Streamlit names the main page after its file,
  *streamlit app*. Renaming it means editing the explorer's main script, which
  this phase deliberately did not touch.
