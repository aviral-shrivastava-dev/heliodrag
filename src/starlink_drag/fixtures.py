"""Synthetic bronze GP history, for developing and testing without credentials.

The real orbital history needs a Space-Track account and an ~8-hour backfill.
That is a poor dependency for a test suite, and an impossible one for CI, so this
module generates a small bronze tree with the same schema and partitioning as the
real loader writes.

It deliberately reproduces the awkward cases the transformations have to survive:

  * several GP records per satellite per day, with metre-scale jitter
  * the *same* (norad_id, epoch) republished in a later partition, which is what
    `stg_gp_history` deduplicates
  * a multi-day gap, which must yield null decay rates rather than a fabricated
    slope across the hole
  * an orbit-raising satellite climbing under thrust, which must be flagged

Generated data is physically plausible but is NOT real. It exists to exercise
the pipeline, never to produce a result.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import numpy as np
import pandas as pd

from . import config

FIXTURE_BRONZE = config.DATA_ROOT / "fixtures" / "bronze"

EARTH_RADIUS_KM = 6378.137
MU = 398_600.4418  # Earth's gravitational parameter, km^3/s^2


def _semimajor_to_mean_motion(sma_km: float) -> float:
    """Convert semi-major axis to mean motion in revolutions per day."""
    period_s = 2 * np.pi * np.sqrt(sma_km**3 / MU)
    return 86_400.0 / period_s


def generate(
    satellites: pd.DataFrame,
    start: dt.date,
    days: int = 60,
    records_per_day: int = 4,
    seed: int = 20260918,
) -> dict[str, pd.DataFrame]:
    """Build one bronze partition per day for the given satellites.

    `satellites` must carry norad_id, satellite_name / name, generation and
    amr_proxy. Decay is scaled by the area-to-mass proxy so the generated data
    has the qualitative structure the analysis looks for -- which makes it useful
    for testing the pipeline and useless for testing the hypothesis.
    """
    rng = np.random.default_rng(seed)
    name_column = "satellite_name" if "satellite_name" in satellites.columns else "name"

    # A gap in the middle, so the centred difference has a hole to handle.
    gap = set(range(days // 2, days // 2 + 3))

    partitions: dict[str, pd.DataFrame] = {}
    state = {
        int(row.norad_id): 6_378.137 + 550.0 + rng.normal(0, 8)
        for row in satellites.itertuples()
    }
    raising = set(satellites["norad_id"].head(1).astype(int))  # one climbing satellite

    for day_index in range(days):
        day = start + dt.timedelta(days=day_index)
        if day_index in gap:
            continue

        rows = []
        for row in satellites.itertuples():
            norad = int(row.norad_id)
            amr = float(getattr(row, "amr_proxy", 1.0) or 1.0)

            if norad in raising:
                drift = +1.2                      # thrusting up to the operational shell
            else:
                drift = -0.045 * amr              # atmospheric decay, scaled by area/mass
            state[norad] += drift + rng.normal(0, 0.004)

            for record in range(records_per_day):
                epoch = dt.datetime.combine(day, dt.time(0, 0)) + dt.timedelta(
                    hours=24 * record / records_per_day
                )
                sma = state[norad] + rng.normal(0, 0.003)
                eccentricity = abs(rng.normal(0.0002, 0.0001))
                rows.append({
                    "NORAD_CAT_ID": norad,
                    "OBJECT_NAME": getattr(row, name_column),
                    "OBJECT_ID": f"SYN-{norad}",
                    "EPOCH": epoch.isoformat(),
                    "MEAN_MOTION": _semimajor_to_mean_motion(sma),
                    "ECCENTRICITY": eccentricity,
                    "INCLINATION": 53.0 + rng.normal(0, 0.01),
                    "RA_OF_ASC_NODE": float(rng.uniform(0, 360)),
                    "ARG_OF_PERICENTER": float(rng.uniform(0, 360)),
                    "MEAN_ANOMALY": float(rng.uniform(0, 360)),
                    "BSTAR": float(abs(rng.normal(3e-4, 5e-5))),
                    "MEAN_MOTION_DOT": float(abs(rng.normal(1e-5, 2e-6))),
                    "SEMIMAJOR_AXIS": sma,
                    "PERIAPSIS": sma * (1 - eccentricity) - EARTH_RADIUS_KM,
                    "APOAPSIS": sma * (1 + eccentricity) - EARTH_RADIUS_KM,
                    "REV_AT_EPOCH": 1000 + day_index * 15 + record,
                    "EPHEMERIS_TYPE": 0,
                })

        partitions[day.isoformat()] = pd.DataFrame(rows)

    # Republish one earlier day's records in a later partition, so the dedup path
    # in stg_gp_history is actually exercised rather than assumed.
    keys = sorted(partitions)
    if len(keys) > 2:
        replayed = partitions[keys[1]].copy()
        replayed["SEMIMAJOR_AXIS"] += 0.001  # a revised solution for the same epochs
        partitions[keys[-1]] = pd.concat([partitions[keys[-1]], replayed], ignore_index=True)

    return partitions


def write(partitions: dict[str, pd.DataFrame], root: pathlib.Path = FIXTURE_BRONZE) -> int:
    """Write partitions in the same layout the real Space-Track loader produces."""
    target = root / "gp_history"
    target.mkdir(parents=True, exist_ok=True)

    total = 0
    for day, frame in partitions.items():
        directory = target / f"epoch_date={day}"
        directory.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(directory / "data.parquet", index=False)
        (directory / "_SUCCESS").write_text("synthetic\n", encoding="utf-8")
        total += len(frame)
    return total


def synthetic_satellites() -> pd.DataFrame:
    """A satellite dimension with the same schema the GCAT build produces.

    CI has no network, so the fixture cannot derive this from the real catalogue.
    The masses and spans mirror the real V2 variants because the whole point of
    the natural experiment is that span is constant while mass is not -- a fixture
    that flattened that would stop testing the thing that matters.
    """
    variants = [
        ("v2-mini",     730.0, 700.0, 29.0, "Starlink V2M",  "stated", "high"),
        ("v2-mini-opt", 575.0, 530.0, 29.0, "Starlink V2MO", "stated", "high"),
        ("v2-mini-dtc", 960.0, 910.0, 29.0, "Starlink V2MD", "stated", "high"),
        ("v1.5",        300.0, 290.0,  9.0, "Starlink", "inferred_mass", "medium"),
    ]

    rows = []
    norad = 60_000
    for generation, mass, dry, span, bus, source, confidence in variants:
        for index in range(4):
            norad += 1
            rows.append({
                "norad_id": norad,
                "jcat": f"S{norad}",
                "name": f"Starlink {norad}",
                "cospar_piece": f"2025-001{chr(65 + index)}",
                "launch_tag": "2025-001",
                "bus": bus,
                "generation": generation,
                "label_source": source,
                "label_confidence": confidence,
                "mass_kg": mass,
                "dry_mass_kg": dry,
                "span_m": span,
                "crude_amr_proxy_span2_over_mass": span**2 / dry,
                "launch_date": pd.Timestamp("2024-12-01"),
                "decay_date": pd.NaT,
                "is_decayed": False,
                "status": "O",
                "perigee_km": 550.0,
                "apogee_km": 555.0,
                "inclination_deg": 53.0,
            })
    return pd.DataFrame(rows)


def synthetic_omni(start: dt.date, days: int, seed: int = 20260918) -> pd.DataFrame:
    """Hourly space weather with the same column names stg_omni expects.

    Includes fill sentinels, so the staging model's null-handling is exercised
    rather than assumed.
    """
    rng = np.random.default_rng(seed)
    periods = days * 24
    times = pd.date_range(start, periods=periods, freq="h", tz="UTC")

    frame = pd.DataFrame({
        "time": times,
        "BZ_GSM1800": rng.normal(0, 4, periods).round(2),
        "V1800": rng.normal(450, 80, periods).round(1),
        "Pressure1800": abs(rng.normal(2.0, 0.8, periods)).round(2),
        "R1800": rng.integers(50, 200, periods),
        "F10_INDEX1800": rng.normal(180, 25, periods).round(1),
        "KP1800": rng.integers(0, 70, periods),
        "DST1800": rng.integers(-150, 20, periods),
        "AE1800": rng.integers(0, 1200, periods),
        "AP_INDEX1800": rng.integers(0, 150, periods),
    })

    # Sprinkle the server's "no data" sentinels through a few rows.
    for column, sentinel in (("F10_INDEX1800", 999.9), ("KP1800", 99),
                             ("DST1800", 99999), ("AP_INDEX1800", 999)):
        frame.loc[rng.choice(periods, size=max(1, periods // 100), replace=False), column] = sentinel

    return frame


def write_all(root: pathlib.Path | None = None, days: int = 60) -> dict[str, int]:
    """Materialise a complete offline fixture: satellites, orbits and weather."""
    root = root or (config.DATA_ROOT / "fixtures")
    start = dt.date(2025, 1, 1)

    satellites = synthetic_satellites()
    interim = root / "interim"
    interim.mkdir(parents=True, exist_ok=True)
    satellites.to_parquet(interim / "starlink_generation_map.parquet", index=False)

    dimension = satellites.rename(columns={"crude_amr_proxy_span2_over_mass": "amr_proxy"})
    orbit_rows = write(generate(dimension, start, days=days), root / "bronze")

    weather = synthetic_omni(start, days=days + 5)
    omni_dir = root / "bronze" / "omni" / f"year={start.year}"
    omni_dir.mkdir(parents=True, exist_ok=True)
    weather.to_parquet(omni_dir / "data.parquet", index=False)
    (omni_dir / "_SUCCESS").write_text("synthetic\n", encoding="utf-8")

    return {
        "satellites": len(satellites),
        "gp_rows": orbit_rows,
        "omni_rows": len(weather),
    }
