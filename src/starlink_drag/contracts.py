"""Ask the upstream APIs whether they still look the way the parsers expect.

Schema drift is the failure this project is least able to notice on its own.
If Space-Track renames a field or NASA retires a parameter, nothing crashes:
the cast produces nulls, the Pandera contract quarantines or passes them, and
every downstream number keeps being computed from progressively emptier data.
The pipeline stays green while the answer rots.

So this checks the *shape* of a live response against what the code reads,
rather than checking that a request succeeds. It is run nightly, and is the
thing the runbook's "schema drift" entry points at.

It fetches the smallest useful response -- one satellite over one day -- so a
nightly check costs almost nothing against the rate limit.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from starlink_drag.clients.hapi import OMNI_PARAMETERS, HapiClient
from starlink_drag.clients.spacetrack import SpaceTrackClient
from starlink_drag.config import Settings
from starlink_drag.schemas import gp as gp_schema
from starlink_drag.schemas import satcat as satcat_schema

PROBE_NORAD_ID = 44713
"""STARLINK-1007, from the first operational launch in November 2019. Chosen
because it is long-lived and certain to have history; any catalogued object
would do."""


@dataclass(frozen=True, slots=True)
class ContractResult:
    """What one upstream source looked like."""

    source: str
    ok: bool
    detail: str
    missing: tuple[str, ...] = ()
    skipped: bool = False
    observed: dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        if self.skipped:
            return f"SKIP  {self.source}: {self.detail}"
        status = "ok  " if self.ok else "DRIFT"
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

    end = dt.date.today()
    start = end - dt.timedelta(days=7)

    with SpaceTrackClient(settings.spacetrack) as client:
        elements = client.gp_history([PROBE_NORAD_ID], start, end)
        catalogue = client.satcat(name_pattern="STARLINK-1007")

    if not elements:
        return ContractResult(
            source="spacetrack",
            ok=False,
            detail=f"no elements for {PROBE_NORAD_ID} in the last 7 days",
        )

    missing_gp = tuple(sorted(set(gp_schema.FIELD_MAP) - set(elements[0])))
    missing_satcat: tuple[str, ...] = ()
    if catalogue:
        missing_satcat = tuple(sorted(set(satcat_schema.FIELD_MAP) - set(catalogue[0])))

    missing = (
        *(f"gp_history.{name}" for name in missing_gp),
        *(f"satcat.{name}" for name in missing_satcat),
    )

    return ContractResult(
        source="spacetrack",
        ok=not missing,
        detail=(
            f"{len(elements)} element sets, all {len(gp_schema.FIELD_MAP)} gp fields "
            f"and {len(satcat_schema.FIELD_MAP)} satcat fields present"
            if not missing
            else f"{len(missing)} field(s) we parse are gone"
        ),
        missing=missing,
    )


def check(settings: Settings, source: str) -> list[ContractResult]:
    """Run the requested checks. ``source`` is 'omni', 'spacetrack' or 'all'."""
    checks = {"omni": check_omni, "spacetrack": check_spacetrack}
    if source == "all":
        return [checks[name](settings) for name in sorted(checks)]
    if source not in checks:
        raise ValueError(f"unknown source {source!r}; expected one of {sorted(checks)} or 'all'")
    return [checks[source](settings)]
