"""Starlink Differential Drag Atlas.

Layering, enforced by review rather than by an import linter:

``science``  pure functions, zero I/O -- orbital mechanics, generation labels
``clients``  the only modules that touch the network
``schemas``  Pandera contracts applied at the ingestion boundary
``defs``     Dagster wiring only; no business logic
``config``   the only reader of the environment
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
