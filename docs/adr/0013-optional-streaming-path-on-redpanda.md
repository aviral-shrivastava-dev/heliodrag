# ADR-0013: An optional streaming path on Redpanda, with the topic as its only state

- **Status:** accepted
- **Date:** 2026-09-26
- **Amends:** [ADR-0006](0006-explorer-reads-gold-through-short-lived-connections.md),
  for one page. The explorer's batch pages still read gold marts only; the live
  page reads the nowcast's own table.

## Context

Phase 6 adds a live, per-generation drag nowcast: a producer publishing current
Starlink element sets and space weather, a consumer keeping a rolling summary,
and a page showing it. Its acceptance has two halves, and the second constrains
everything: the nowcast updates live, **and the batch path is unchanged**.

The research question does not need this. It compares generations across a
solar cycle from daily data; nothing in it wants sub-daily latency. The phase
was built on the owner's explicit confirmation, as a demonstration of the
streaming half of a data platform, and is scoped so it can be ignored.

## Decision

**Redpanda**, `v26.2.3`, in one container. It speaks the Kafka protocol, so the
producer and consumer use the standard `confluent-kafka` client and would run
unchanged against Kafka, MSK or Confluent Cloud. It is one process with no
ZooKeeper or KRaft quorum, and runs in 512 MB beside everything else.

**Three topics.** Element sets, keyed by NORAD ID so each satellite's arrive in
order; space weather, keyed by quantity; and refused records with their
reasons, kept longer, as batch quarantine keeps them.

**The topic is the consumer's state.** The consumer commits no offsets and keeps
no checkpoint. On start it rewinds to the oldest retained message and rebuilds
the nowcast; the computation is pure and ignores repeats, so a replay reaches
the state the live run had (tested). Seven days' retention bounds the replay
and is far longer than the 24-hour window. There is nothing to corrupt or
migrate, and a restart is the recovery procedure.

**Event time, not wall-clock time.** The window is the last 24 hours of element-set
epochs up to the newest one seen. A replay then computes what the live run did,
and a stalled feed shows as a stale epoch on the page rather than as a nowcast
quietly emptying.

**Its own table**, `stream/stream_drag_nowcast`, written through the same dlt
path as bronze but in a separate dataset. No warehouse view is built over it,
so dbt never sees it.

**Isolation, enforced rather than promised.**

- The Kafka client is in a `stream` dependency group that the batch image does
  not install; the stream commands import it lazily. A test imports the Dagster
  definitions and the CLI in a fresh interpreter and fails if any streaming
  module, or the Kafka client, is loaded.
- The streaming services carry a Compose profile. `docker compose up` starts
  exactly the batch stack; its resolved configuration was diffed against the
  previous commit's and is identical.
- The batch tests were not edited. The stream tests are new files, and the
  broker tests run in their own CI job.

**Rate limits are respected by construction.** Space-Track asks that current
element sets be fetched at most hourly; the poll interval has a floor of 60
minutes in the settings, so it cannot be configured lower. Each poll is one
request plus a login, through the shared rate-limit ledger (ADR-0008). Inside
Docker that ledger counts only the container's requests -- two an hour, well
inside any headroom the host's ingestion leaves.

## Alternatives considered

- **Apache Kafka.** The reference implementation, and the same client code. It
  needs a KRaft controller as well as a broker, and more memory. Nothing here
  uses a feature Redpanda lacks.
- **A persistent consumer state store** (RocksDB, a checkpoint table). Needed
  when state outgrows a replay. This state is one element set per satellite
  plus the last value of five indices; replaying a week of it takes seconds.
- **Committing offsets.** Would make a restart skip the replay and start with
  an empty state -- a nowcast with no history until every satellite reported
  twice again.
- **Writing every message to Iceberg.** Thousands of commits a day of a few
  rows each. Snapshots every 15 minutes (configurable) keep the table small; an
  Iceberg commit per snapshot is still the main cost, and compaction is future
  work.

## Consequences

- One more image, three more containers, and roughly 750 MB of memory when the
  profile is up. None of it when it is not.
- The live page is the only part of the explorer that does not read gold marts.
- Redpanda Community is source-available under the Business Source License, not
  open source. Running it for this is permitted; offering Redpanda itself as a
  service would not be. Any Kafka-compatible broker can replace it.
- Altitude rates need two element sets per satellite at least six hours apart,
  so after a cold start with an empty topic the rate columns fill in over the
  first hours. BSTAR is available from the first message.
