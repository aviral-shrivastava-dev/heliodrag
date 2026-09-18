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

## Superseded reasoning

An earlier version of this record also argued that Airflow does not run natively
on Windows. That was tested and is not a real constraint: Docker Desktop runs the
Airflow stack fine on this machine (16 CPUs, 8.1 GB allocated to the VM), and
`docker-compose.airflow.yml` now does exactly that. The claim has been removed
rather than quietly softened, because it influenced a decision it should not have.

The asset-model and partitioning arguments above stand on their own.

## Consequences

- Fewer postings name Dagster than Airflow, so the choice must be defensible in
  interview rather than assumed. The concepts transfer.
- Both orchestrators are now implemented, and neither owns any logic: each calls
  the same `starlink_drag.cli.*` entrypoints and the same dbt project. That is
  the property that made adding Airflow cheap, and it is the point worth making
  about the design -- an orchestrator is a scheduling and observability concern,
  not a place to put business logic.
