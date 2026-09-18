# ADR-0002: Dagster over Airflow

- **Status:** accepted
- **Date:** 2026-09-18

## Context

The pipeline is a set of dated artefacts, not a set of chores. For every day
from 2020 to the present there is one partition of Space-Track general
perturbations data, one partition of OMNI space-weather data, and a chain of
dbt models derived from them. The operational requirements follow from that
shape:

- **Backfill is the normal case, not an exception.** The first real run
  processes roughly six years of daily partitions. Space-Track's rate limits
  (fewer than 30 requests per minute, fewer than 300 per hour) mean that run
  takes many hours and will be interrupted. Resuming must not mean re-running
  what already succeeded.
- **Per-partition idempotency is a correctness requirement.** Re-running
  `2024-05-11` must replace exactly that partition. A scientific result that
  changes depending on how many times the pipeline ran is not a result.
- **Lineage has to be visible.** A reviewer asking "where did this number come
  from" should be able to trace a figure back through the marts to a specific
  raw partition without reading the scheduler's source.
- **One developer, one laptop.** Operational overhead is a direct tax on the
  time available for the actual research question.

An earlier iteration of this repository ran an Airflow DAG alongside Dagster.
Two orchestrators covering the same work is worse than either alone: the
definitions drift, and a reviewer cannot tell which one is authoritative.

## Decision

Use **Dagster**, with a daily `TimeWindowPartitionsDefinition` and
software-defined assets, as the only orchestrator.

- Assets name the artefact produced (`bronze_gp_history`, `fct_daily_decay`),
  so the partition key is part of the asset's identity and re-materialising a
  partition is the ordinary operation rather than a bespoke script.
- `dagster-dbt` surfaces every dbt model as an asset, giving one lineage graph
  across ingestion and transformation instead of a task that shells out to dbt.
- Asset checks express freshness, row-count and null-rate expectations next to
  the asset they describe.
- `dagster dev` is a single local process, with no scheduler, webserver,
  metadata database and worker to stand up first.

## Alternatives considered

**Airflow.** The honest case for Airflow is strong and mostly non-technical: it
appears in far more job postings than every other orchestrator combined, it is
what most interviewers will ask about, and a reviewer skimming this repository
may read the absence of Airflow as unfamiliarity with the industry standard
rather than as a decision. That is a real cost of this record, and it is why
this ADR exists.

The technical case against it, for this project: Airflow orchestrates tasks, not
data. Expressing "this partition of this table is stale" requires convention —
datasets, sensors, and naming discipline — rather than being the primitive. The
local footprint is several services. Backfills over a dated range work, but the
idempotency guarantee lives in the task's own code rather than in the framework.

**Prefect.** A closer match than Airflow and pleasant to develop against, but
its data-asset and partition story is thinner than Dagster's, which is precisely
the part being leaned on here.

**A CLI driven by cron.** Genuinely sufficient for the batch path, and it is what
the project falls back to if Dagster is ever removed. Rejected as the primary
because it provides no lineage, no partition status, no retry semantics and no
place to hang data-quality checks — all of which would then have to be built.

## Consequences

- **Reviewers expecting Airflow will not find it.** Mitigated two ways: this
  record states the trade-off plainly, and `src/starlink_drag/cli.py` exposes
  every pipeline operation so that Airflow, Prefect or cron could drive the same
  code paths without touching business logic. The orchestrator is replaceable;
  that is the point of keeping `defs/` free of logic.
- Dagster's API has moved faster than Airflow's. Version constraints are pinned
  in `uv.lock`, and upgrades are deliberate commits.
- Fewer managed hosting options than Airflow, which has an offering from every
  major cloud. Not a constraint at this project's scale; it would be at an
  employer's.
- The previous Airflow DAG and its compose file were removed rather than left in
  place. They remain in git history at commit `1ba1cb5` if the decision is ever
  revisited.
