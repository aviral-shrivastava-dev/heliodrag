"""Backfill CLI: validate the Space-Track query, then load a scoped window.

    python -m starlink_drag.backfill --smoke
    python -m starlink_drag.backfill --window v2-overlap --dry-run
    python -m starlink_drag.backfill --window v2-overlap

The smoke test exists because the full backfill is an ~8-hour commitment against
a rate-limited API. Discovering a malformed query or an unexpected schema twenty
minutes in wastes both the time and a chunk of the hourly request budget, so a
single-day request is checked end to end first.

Windows are scoped to the analysis rather than to the whole catalogue. The V2
natural experiment -- the three V2 Mini variants sharing a 29 m span while
differing in mass by 1.67x -- only exists once all three are on orbit, which cuts
the job from ~2,700 days to ~660.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

import pandas as pd

from . import config
from .ingest import spacetrack

log = logging.getLogger(__name__)

# Window boundaries come from the generation map's own launch dates.
WINDOWS = {
    "v2-overlap": (
        dt.date(2024, 11, 25),
        "All three V2 Mini variants on orbit together: the only comparison where "
        "geometry and epoch are both controlled.",
    ),
    "v2-era": (
        dt.date(2023, 2, 27),
        "From the first v2-mini launch. Adds v2-mini history before the Optimized "
        "variant existed.",
    ),
    "full": (
        dt.date.fromisoformat(config.HISTORY_START),
        "Every Starlink satellite ever flown, from the v0.9 batch.",
    ),
}

# Columns the downstream staging model casts. If Space-Track stops returning one
# of these, the run should fail here rather than producing a mart full of nulls.
REQUIRED_COLUMNS = {
    "NORAD_CAT_ID", "OBJECT_NAME", "EPOCH", "MEAN_MOTION",
    "ECCENTRICITY", "INCLINATION", "SEMIMAJOR_AXIS", "PERIAPSIS", "APOAPSIS",
}


def smoke_test(day: dt.date | None = None) -> int:
    """Fetch and validate a single day. Returns a process exit code.

    Costs one request. Everything the long backfill depends on is checked here:
    authentication, query syntax, response schema, value sanity, and whether the
    returned satellites actually join to the generation dimension.
    """
    day = day or (dt.date.today() - dt.timedelta(days=2))
    print(f"Smoke test: fetching {day} (1 request)\n")

    client = spacetrack.SpaceTrackClient()
    try:
        client.login()
    except Exception as error:
        print(f"FAIL  authentication: {error}")
        return 1
    print("ok    authenticated")

    try:
        frame = client.gp_history_for_day(day)
    except Exception as error:
        print(f"FAIL  query: {error}")
        return 1
    print(f"ok    query returned {len(frame)} rows")

    if frame.empty:
        print("FAIL  no rows returned -- check the date is not in the future")
        return 1

    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        print(f"FAIL  missing columns: {sorted(missing)}")
        print(f"      got: {sorted(frame.columns)}")
        return 1
    print(f"ok    all {len(REQUIRED_COLUMNS)} required columns present")

    satellites = frame["NORAD_CAT_ID"].nunique()
    print(f"ok    {satellites} distinct satellites, "
          f"{len(frame) / max(satellites, 1):.1f} records each")

    # Values must be physically plausible, not merely present.
    sma = pd.to_numeric(frame["SEMIMAJOR_AXIS"], errors="coerce")
    if not sma.between(6_500, 7_500).mean() > 0.9:
        print(f"FAIL  semi-major axis out of range: {sma.min():.0f}-{sma.max():.0f} km")
        return 1
    print(f"ok    semi-major axis {sma.min():.0f}-{sma.max():.0f} km (plausible LEO)")

    # The whole project depends on this join, so prove it works on real IDs.
    mapping_path = config.DATA_ROOT / "interim" / "starlink_generation_map.parquet"
    if mapping_path.exists():
        mapping = pd.read_parquet(mapping_path)
        returned = set(pd.to_numeric(frame["NORAD_CAT_ID"], errors="coerce").dropna().astype(int))
        matched = returned & set(mapping["norad_id"].dropna().astype(int))
        pct = 100 * len(matched) / max(len(returned), 1)
        status = "ok  " if pct > 90 else "WARN"
        print(f"{status}  {len(matched)}/{len(returned)} ({pct:.1f}%) join to dim_satellite")
        if pct <= 90:
            print("      unmatched IDs are usually satellites launched since the "
                  "last GCAT refresh -- re-run `python -m starlink_drag.build`")
    else:
        print("WARN  generation map not built; run `python -m starlink_drag.build`")

    print("\nSmoke test passed. Safe to start the backfill.")
    return 0


def plan(window: str) -> tuple[dt.date, dt.date, int, int]:
    """Return (start, end, days_remaining, estimated_minutes) for a window."""
    start, _ = WINDOWS[window]
    end = dt.date.today()

    total = (end - start).days
    done = {d for d in spacetrack.completed_days() if start <= d < end}
    remaining = total - len(done)
    minutes = round(60 * remaining / config.SPACETRACK_MAX_PER_HOUR)
    return start, end, remaining, minutes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--smoke", action="store_true",
                        help="fetch and validate a single day, then exit")
    parser.add_argument("--window", choices=sorted(WINDOWS), default="v2-overlap",
                        help="which analysis window to load (default: v2-overlap)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be fetched without fetching")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.smoke:
        return smoke_test()

    start, end, remaining, minutes = plan(args.window)
    _, description = WINDOWS[args.window]

    print(f"Window '{args.window}': {start} .. {end}")
    print(f"  {description}")
    print(f"  {(end - start).days} days total, {remaining} still to fetch")
    print(f"  estimated {minutes} min ({minutes / 60:.1f} h) at "
          f"{config.SPACETRACK_MAX_PER_HOUR} requests/hour")

    if args.dry_run:
        print("\nDry run; nothing fetched.")
        return 0

    if remaining == 0:
        print("\nNothing to do.")
        return 0

    print("\nStarting. Safe to interrupt -- completed days are skipped on re-run.\n")
    logging.getLogger("starlink_drag").setLevel(logging.INFO)
    summary = spacetrack.backfill(start, end)
    print(f"\nDone: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
