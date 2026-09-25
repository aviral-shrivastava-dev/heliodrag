"""Ask the upstream APIs whether they still look the way the parsers expect.

Schema drift is the failure this project is least able to notice on its own.
If Space-Track renames a field or NASA retires a parameter, nothing crashes:
the cast produces nulls, the Pandera contract quarantines or passes them, and
every downstream number keeps being computed from progressively emptier data.
The pipeline stays green while the answer rots.

So this checks the *shape* of a live response against what the code reads,
rather than checking that a request succeeds. It is run nightly, and is the
thing the runbook's "schema drift" entry points at.

It costs two Space-Track requests: the Starlink catalogue, and one week of
elements for a handful of satellites chosen from it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from starlink_drag.clients.hapi import OMNI_PARAMETERS, HapiClient
from starlink_drag.clients.spacetrack import SpaceTrackClient
from starlink_drag.config import Settings
from starlink_drag.schemas import gp as gp_schema
from starlink_drag.schemas import satcat as satcat_schema

PROBE_COUNT = 5
"""How many satellites to ask for. More than one, so a single satellite being
deorbited or losing tracking cannot fail the check on its own."""

PROBE_MIN_AGE_DAYS = 30
"""Probes must have launched at least this long ago. The newest few satellites
usually share one launch, and a launch from the last few days may not have a
week of elements yet -- so without this floor they would all come back empty
together."""

LOOKBACK_DAYS = 7


@dataclass(frozen=True, slots=True)
class ContractResult:
    """What one upstream source looked like."""

    source: str
    ok: bool
    detail: str
    missing: tuple[str, ...] = ()
    skipped: bool = False
    empty: bool = False
    """The source answered with nothing, so the shape could not be checked.
    Not a pass -- but not evidence of drift either, because an empty 200 is
    also how Space-Track answers when it is throttling."""
    observed: dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        if self.skipped:
            return f"SKIP  {self.source}: {self.detail}"
        status = "ok  " if self.ok else "DRIFT"
        if self.empty:
            status = "EMPTY"
        line = f"{status}  {self.source}: {self.detail}"
        if self.missing:
            line += f"\n      missing: {', '.join(self.missing)}"
        return line


def check_omni(settings: Settings) -> ContractResult:
    """Does the HAPI dataset still declare the parameters we read?"""
    with HapiClient(settings.hapi) as client:
        info = client.info()
        declared = {parameter["name"] for parameter in info["parameters"]}
        fills = client.fill_values()

    wanted = {"Time", *OMNI_PARAMETERS}
    missing = tuple(sorted(wanted - declared))

    # A changed fill marker is the quiet one: values keep arriving, and a
    # sentinel the parser no longer recognises enters the science as data.
    observed = {name: fills.get(name, "none") for name in sorted(OMNI_PARAMETERS)}

    return ContractResult(
        source="nasa.omni",
        ok=not missing,
        detail=(
            f"{len(declared)} parameters declared, all {len(wanted)} we read are present"
            if not missing
            else f"{len(missing)} parameter(s) we read are gone"
        ),
        missing=missing,
        observed=observed,
    )


def choose_probes(
    catalogue: Sequence[dict[str, Any]], today: dt.date, count: int = PROBE_COUNT
) -> list[int]:
    """The newest satellites that are still on orbit and past their first month.

    Chosen from the live catalogue on every run, never hard-coded. The first
    version of this check probed STARLINK-1007, which re-entered on 2024-10-02;
    a satellite that no longer exists has no elements, so every run reported
    drift that was not there. Any fixed choice eventually does the same.

    Newest, because a recently launched satellite is the least likely to be
    being commanded down.
    """
    cutoff = (today - dt.timedelta(days=PROBE_MIN_AGE_DAYS)).isoformat()
    on_orbit = [
        row
        for row in catalogue
        if not row.get("DECAY") and row.get("LAUNCH") and str(row["LAUNCH"]) <= cutoff
    ]
    on_orbit.sort(key=lambda row: str(row["LAUNCH"]), reverse=True)
    return [int(row["NORAD_CAT_ID"]) for row in on_orbit[:count]]


def check_spacetrack(settings: Settings) -> ContractResult:
    """Does gp_history still carry the fields we parse, and satcat too?

    Skips rather than fails when credentials are absent, so a fork or a
    contributor without an account gets a clean result instead of a red build.
    """
    if not settings.spacetrack.is_configured:
        return ContractResult(
            source="spacetrack",
            ok=True,
            skipped=True,
            detail="no credentials configured; nothing checked",
        )

    today = dt.date.today()
    start = today - dt.timedelta(days=LOOKBACK_DAYS)

    with SpaceTrackClient(settings.spacetrack) as client:
        catalogue = client.satcat()
        probes = choose_probes(catalogue, today)
        elements = client.gp_history(probes, start, today) if probes else []

    if not catalogue:
        return _empty("the Starlink catalogue came back empty")

    missing_satcat = tuple(sorted(set(satcat_schema.FIELD_MAP) - set(catalogue[0])))
    satcat_missing = tuple(f"satcat.{name}" for name in missing_satcat)

    if not probes:
        # A catalogue with no qualifying satellite means LAUNCH or DECAY no
        # longer mean what we read them as -- drift, not an empty answer.
        return ContractResult(
            source="spacetrack",
            ok=False,
            detail="no on-orbit satellite in the catalogue qualifies as a probe",
            missing=satcat_missing,
        )

    if not elements:
        ids = ", ".join(str(norad_id) for norad_id in probes)
        return _empty(
            f"no elements in the last {LOOKBACK_DAYS} days for any of {len(probes)} "
            f"on-orbit satellites ({ids}); throttling looks like this"
        )

    missing_gp = tuple(sorted(set(gp_schema.FIELD_MAP) - set(elements[0])))
    missing = (*(f"gp_history.{name}" for name in missing_gp), *satcat_missing)

    return ContractResult(
        source="spacetrack",
        ok=not missing,
        detail=(
            f"{len(elements)} element sets from {len(probes)} satellites, all "
            f"{len(gp_schema.FIELD_MAP)} gp fields and {len(satcat_schema.FIELD_MAP)} "
            "satcat fields present"
            if not missing
            else f"{len(missing)} field(s) we parse are gone"
        ),
        missing=missing,
    )


def _empty(detail: str) -> ContractResult:
    return ContractResult(source="spacetrack", ok=False, empty=True, detail=detail)


def check(settings: Settings, source: str) -> list[ContractResult]:
    """Run the requested checks. ``source`` is 'omni', 'spacetrack' or 'all'."""
    checks = {"omni": check_omni, "spacetrack": check_spacetrack}
    if source == "all":
        return [checks[name](settings) for name in sorted(checks)]
    if source not in checks:
        raise ValueError(f"unknown source {source!r}; expected one of {sorted(checks)} or 'all'")
    return [checks[source](settings)]
