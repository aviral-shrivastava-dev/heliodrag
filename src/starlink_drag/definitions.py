"""Dagster entry point.

``dagster dev -m starlink_drag.definitions`` loads this module. It stays
deliberately thin: assets, resources, schedules and checks are defined under
``starlink_drag.defs`` and only assembled here.

Phase 3 populates this. Until then it loads cleanly and shows an empty graph,
which is honest -- there is nothing to orchestrate yet.
"""

from __future__ import annotations

from dagster import Definitions

defs = Definitions()
