# Phase documentation

Every phase of this project is documented twice, in the same directory:

| File | Written for |
| --- | --- |
| `phase-N.md` | Someone who builds data pipelines. Assumes you know what a lockfile, a partition and a type checker are. |
| `phase-N-plain.md` | Someone who does not. Assumes nothing at all. |

**The plain-language versions are not simplified to the point of being wrong.**
They use fewer assumed words, not fewer facts. Where a real term exists, it is
introduced rather than avoided — you should be able to read the plain version,
then read the technical one, and find that nothing contradicts.

Start here:

- [What this project is](overview-plain.md) — plain language
- [What this project is](overview.md) — technical

## Phases

| Phase | Scope | Technical | Plain |
| --- | --- | --- | --- |
| 0 | Scaffold: packaging, tooling, CI, ADRs | [phase-0.md](phase-0.md) | [phase-0-plain.md](phase-0-plain.md) |
| 1 | Ingestion: API clients, dlt into bronze Iceberg | written when built | |
| 2 | Transformation: dbt staging, intermediate, marts | | |
| 3 | Orchestration: partitioned Dagster assets | | |
| 4 | Hardening: coverage, nightly CI, runbook, Terraform | | |
| 5 | Serving: Streamlit, full README, dbt docs | | |
| 6 | Streaming (optional): Redpanda nowcast | | |
| 7 | Analysis: regression, bootstrap CIs, figures | | |

A phase's pair of documents is written **when that phase is built**, not before.
Documentation for work that does not exist yet is a description of an intention,
and intentions change.

## What goes in each phase document

Both versions cover the same ground in the same order:

1. **What this phase is for** — the problem it solves, not the files it adds.
2. **What was built** — every file, and what it does.
3. **Why it was built that way** — the decisions, including the rejected options.
4. **How to check it works** — commands, with the results actually observed.
5. **What is deliberately missing** — and which phase supplies it.
6. **What is genuinely unresolved** — open questions, honestly stated.

Architecture decisions that outlive a single phase live in
[`docs/adr/`](../adr/) instead, one file per decision.
