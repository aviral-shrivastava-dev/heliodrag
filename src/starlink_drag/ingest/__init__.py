"""Ingestion: fetch strategy, validation and landing into bronze.

This sits outside ``defs/`` on purpose. ``defs/`` is Dagster wiring with no
business logic, and deciding how wide to fetch, how to chunk under a rate limit
and how to land a partition is business logic. Phase 3's Dagster assets call
into this package rather than reimplementing it, which is what keeps the
orchestrator replaceable.
"""

from __future__ import annotations
