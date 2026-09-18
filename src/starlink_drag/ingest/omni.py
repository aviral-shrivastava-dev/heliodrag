"""Land NASA OMNI space weather into bronze.

OMNI is public, unrated and small -- a decade of hourly records is well under a
million rows -- so it is fetched in one span and partitioned on write.
"""

from __future__ import annotations

import datetime as dt

from starlink_drag.clients.hapi import HapiClient
from starlink_drag.config import Settings
from starlink_drag.ingest.bronze import OMNI_SPEC, LoadOutcome, land
from starlink_drag.schemas import omni


def ingest_omni(
    settings: Settings,
    start: dt.date,
    end: dt.date,
    *,
    client: HapiClient | None = None,
) -> LoadOutcome:
    """Land hourly OMNI records over ``[start, end)`` into bronze."""
    owned = client is None
    hapi = client or HapiClient(settings.hapi)
    try:
        rows = hapi.omni(start, end)
    finally:
        if owned:
            hapi.close()

    frame = omni.to_frame(rows)
    return land(frame, OMNI_SPEC, settings, schema=omni.schema, source=omni.SOURCE)
