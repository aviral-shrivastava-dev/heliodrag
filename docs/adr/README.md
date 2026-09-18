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
