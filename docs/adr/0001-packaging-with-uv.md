# ADR-0001: Packaging with uv and a src layout

- **Status:** accepted
- **Date:** 2026-09-18

## Context

This repository has two readers with different failure modes.

A hiring manager clones it, runs one command, and forms a judgement within a few
minutes. A slow or fragile install is the whole review.

A journal reviewer needs the numerical result to be reproducible years from now.
That requires an exact, committed record of every transitive dependency, not a
range that resolves differently each time it is installed.

The project also pulls in three tools with historically awkward dependency
footprints — Dagster, dbt-core and dlt — which pin overlapping libraries.
Dependency resolution needs to fail loudly at install time rather than quietly
at runtime.

## Decision

- **uv** for environment and dependency management, with `uv.lock` committed.
- **`src/` layout**, so tests import the installed package rather than the
  working directory. An import that works only because of the current directory
  is a bug that surfaces in CI, not on a laptop.
- **hatchling** as the build backend.
- **Python 3.12**, pinned as `>=3.12,<3.13` and installed by uv itself, so the
  interpreter is part of the reproducible environment rather than whatever the
  machine happens to have.

## Alternatives considered

**Poetry.** Mature, widely understood, and it also produces a lockfile. It
resolves an order of magnitude more slowly, which matters because CI runs it on
every push, and its dependency-group model has changed shape more than once.

**pip-tools with a plain venv.** Fewer moving parts and no new tool to install.
It needs a separate mechanism to manage the interpreter version, and the
`requirements.in` / `requirements.txt` split has to be maintained by hand across
dev and runtime groups.

**conda / mamba.** The usual choice in scientific computing and it would handle
binary scientific dependencies well. This project's numerical surface is small —
the heavy lifting is in DuckDB and Arrow, both of which ship good wheels — so the
extra environment machinery buys little, and conda environments are harder for a
data engineer reviewer to evaluate quickly.

**Python 3.13.** The system interpreter on the development machine. Rejected for
now: parts of the stack still publish wheels and support matrices that lag the
newest release, and a portfolio project that fails to install is worse than one
on an interpreter a year old.

## Consequences

- Contributors must install uv. This is one command and is documented in the
  README, but it is a real prerequisite and not everyone will have it.
- `uv.lock` is uv-specific. If the project ever needs a portable artefact,
  `uv export --format requirements-txt` produces one; we do not commit that file
  as well, because two lockfiles drift.
- uv is young and its CLI is still changing. CI pins an exact uv version so that
  an upstream release cannot break a build without an explicit commit here.
- Pinning to 3.12 means we must revisit this record deliberately rather than
  drifting forward. That is the intent.
