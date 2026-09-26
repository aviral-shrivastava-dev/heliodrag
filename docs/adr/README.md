# Architecture decision records

One file per decision, numbered and immutable. If a decision changes, add a new
record that supersedes the old one rather than editing history — a reviewer
needs to see what was believed at the time.

Template:

```markdown
# ADR-NNNN: <short title>

- **Status:** proposed | accepted | superseded by ADR-NNNN
- **Date:** YYYY-MM-DD

## Context
What forced a decision. Constraints, not preferences.

## Decision
What we are doing, in one or two sentences.

## Alternatives considered
Each with the reason it lost. If an alternative has no real downside, it
probably should have won.

## Consequences
What this costs us, including what it makes harder. A record with no negative
consequences is not finished.
```

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-packaging-with-uv.md) | Packaging with uv and a src layout | accepted |
| [0002](0002-dagster-over-airflow.md) | Dagster over Airflow | accepted |
| [0003](0003-sliding-window-rate-limiter.md) | A sliding-window rate limiter, not a token bucket | partly superseded by 0008 |
| [0004](0004-bronze-partition-replace-and-byte-identity.md) | Bronze replaces partitions; provenance lives outside the data files | partly superseded by 0005 |
| [0005](0005-bronze-appends-rather-than-replaces.md) | Bronze appends rather than replaces partitions | accepted |
| [0006](0006-explorer-reads-gold-through-short-lived-connections.md) | The explorer reads gold marts only, through short-lived connections | accepted |
| [0007](0007-publish-schema-and-aggregates-never-element-data.md) | Publish the schema and aggregates, never element-level data | accepted |
| [0008](0008-rate-limit-shared-across-processes-and-resumable-fetch.md) | The rate limit is shared across processes, and the element fetch resumes | accepted |
| [0009](0009-bounded-memory-warehouse-build.md) | The warehouse build runs in bounded memory | accepted |
| [0010](0010-one-lake-module-for-every-storage-backend.md) | One lake module, so every tool reaches S3 storage the same way | accepted |
