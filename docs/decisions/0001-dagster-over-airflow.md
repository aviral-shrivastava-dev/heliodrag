# 1. Dagster over Airflow

Date: 2026-09-18 · Status: Accepted

## Context

The pipeline needs orchestration. Airflow appears in ~29% of data-engineering
postings and is the default assumption in most teams; Dagster is less common.

## Decision

Dagster.

## Rationale

The unit of work here is a *table*, not a task. `gp_history`, `dim_satellite` and
each dbt model are assets with a materialised state, and Dagster's asset model
expresses that directly — the lineage graph it renders is the real dependency
structure rather than a hand-maintained DAG that happens to mirror it.

The deciding practical factor is partitioning. A 662-day backfill against a
rate-limited API needs per-day status, per-day retry, and resume-after-failure.
Dagster's `DailyPartitionsDefinition` gives that natively and maps onto the
loader's own `_SUCCESS` markers. The Airflow equivalent is achievable but is more
machinery for the same result.

Airflow also does not run natively on Windows, which is the development
environment here; it would require WSL or Docker for everyday work.

## Consequences

- Fewer postings name Dagster than Airflow, so the choice must be defensible in
  interview rather than assumed. The concepts transfer.
- If an Airflow DAG is later wanted for demonstration, it can wrap the same CLI
  entrypoints without touching the pipeline.
