# Roadmap: from working pipeline to interview-ready

What the project has, what it lacks, and the order to close the gaps. Derived
from 2026 posting analyses, DE system-design interview guides and portfolio
review criteria — see [sources](#sources).

## The bar

Two findings recur across every source and shape everything below.

> The senior-vs-mid signal is whether you raise failure modes **before** the
> interviewer prompts you.

> Interviewers assess idempotency and backfill strategies because these are the
> problems that cause actual production outages. How you handle duplicate records
> reveals whether someone has operated a pipeline or only read about building one.

And the most common red flag: *"strong portfolios don't showcase tools or
quantity — they demonstrate system design, trade-offs, and real-world engineering
thinking."* Adding tools for their own sake moves the project backwards.

## Scorecard

### Already strong

| Criterion | Evidence |
|---|---|
| Idempotency | `_SUCCESS` markers, temp-file-then-rename, `delete+insert`; unit-tested |
| Backfill | Resumable, scoped windows, cost-estimated before running |
| Late-arriving data | Lookback over watermark, with the reasoning recorded (ADR 2) |
| Failure modes | Four ADRs, raised unprompted |
| Data quality | 54 dbt tests, a bespoke validation suite, a coverage audit model |
| Anomaly handling | Labelled not filtered; tests scoped rather than widened (ADR 3) |
| Real problem | Published research gap, with an honest negative finding |
| Orchestration | Dagster and Airflow, neither owning logic |
| CI/CD | Full `dbt build` per PR against a synthetic fixture, no credentials |

### Gaps, in priority order

| # | Gap | Why it matters | Effort | Cost |
|---|---|---|---|---|
| 1 | **SCD2 dimension** | Named discriminator in interview guides. The dimension genuinely changes. | ~2h | free |
| 2 | **Data contracts** | 2026 expectation: schema violations rejected, not discovered downstream | ~1h | free |
| 3 | **Cloud object storage + IaC** | AWS 44% of postings; Terraform 14%; makes the lake real | ~3h | free tier |
| 4 | **Dashboard / live demo** | Visualization 34%; every portfolio guide wants a live link | ~4h | free |
| 5 | **Observability** | Five pillars: freshness, volume, schema, distribution, lineage. Have 2/5. | ~3h | free |
| 6 | **Write-Audit-Publish** | Tests currently run *after* marts are built — bad data lands first | ~3h | free |
| 7 | **Streaming consumer** | Kafka 17%; Redpanda already in compose, nothing consumes it | ~4h | free |
| 8 | **Table format (Iceberg)** | Time travel, snapshot isolation, schema evolution | ~4h | free |
| 9 | **Spark** | 33% of postings, but see ADR 4 — needs a container here | ~4h | disk |

## Detail on the top four

### 1. SCD2 on `dim_satellite`

Currently a static snapshot: rebuild it and yesterday's state is gone. But the
dimension really does change — satellites decay, GCAT revises masses and spans,
and generation labels move as the catalogue is curated. Today those changes are
silently overwritten.

`dbt snapshot` gives Type 2 directly, with `valid_from` / `valid_to`. This is
worth doing for a real reason, not for the label: `fct_satellite_day` currently
joins every historical day against *today's* dimension, so if GCAT reclassifies a
satellite, history silently rewrites itself. SCD2 fixes an actual correctness bug.

Follow-up worth being able to discuss: Type 6 (hybrid) if both historical and
current attribute views are needed.

### 2. Data contracts

dbt's `contract: enforced` fails the build when a model's shape drifts from its
declared schema, rather than letting the change propagate and surface as a
confusing downstream failure. Apply to `dim_satellite` and `fct_satellite_day`.

Pairs with `dbt source freshness` on the bronze sources — the project already
*found* a freshness problem (GCAT lagging Space-Track by ten weeks) and currently
has no mechanism that would catch it recurring.

### 3. Cloud object storage + Terraform

Bronze is a local directory. Moving it to S3-compatible storage (Cloudflare R2's
10 GB free tier, or AWS free tier) closes the single most-requested skill and
changes how the project reads: "partitioned Parquet on object storage, provisioned
as code" is a different sentence from "files on my laptop."

DuckDB reads `s3://` directly via httpfs, so the dbt layer needs almost no change.
Terraform then has something real to provision.

### 4. Dashboard

The deliverable is currently a `.duckdb` file. Every portfolio guide asks for a
live link. Streamlit Community Cloud is free; MotherDuck's free tier (10 GB) gives
a hosted DuckDB the dashboard can read without shipping the database.

Content should lead with the finding, including the negative one — the
station-keeping result is more interesting than a decay chart.

## What NOT to do

- **Don't add Spark to say you used Spark.** At ~12M rows DuckDB is correct.
  "I'd reach for Spark past roughly a hundred million rows" is a better answer
  than an unnecessary PySpark job, and interviewers notice resume-driven tooling.
- **Don't build more pipelines.** One deep project beats several shallow ones.
- **Don't hide the station-keeping finding.** It is the strongest evidence in the
  repo that the work was actually run rather than merely built.

## Sources

- [Data Engineer Skills Companies Want in 2026: 6,877-posting analysis](https://dev.to/gnana_6392e836fd500a957dc/data-engineer-skills-companies-want-in-2026-6877-posting-analysis-44p9)
- [DE Interview Prep: rounds, questions, plan](https://datadriven.io/data-engineer-interview-prep)
- [DE System Design Interview framework](https://www.startdataengineering.com/post/de_interview_sd/)
- [Data Engineering Portfolio: projects that get hired](https://www.dataexpert.io/blog/data-engineering-portfolio-projects-get-hired)
- [Portfolio Review Checklist 2026](https://dataengineeracademy.com/blog/data-engineer-portfolio-review-checklist-2026-what-hiring-managers-actually-score/)
- [dbt: How we structure our projects](https://docs.getdbt.com/best-practices/how-we-structure/1-guide-overview)
- [Write-Audit-Publish with Iceberg](https://www.telm.ai/blog/what-is-write-audit-publish-in-apache-iceberg-and-why-it-matters-for-data-quality/)
- [Data observability in 2026: tools, metrics, practices](https://www.dqlabs.ai/blog/the-definitive-guide-for-data-observability-2026/)
