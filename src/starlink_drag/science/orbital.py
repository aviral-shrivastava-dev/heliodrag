"""Orbital mechanics. Pure functions, no I/O, no logging, no configuration.

Everything here is a closed-form conversion between quantities Space-Track
publishes and the quantities the research question needs. Keeping it free of I/O
means it can be tested exhaustively and offline against values that are known
independently -- which is the only reason to trust a derived decay rate at all.

Conventions
-----------
Mean motion ``n`` is in revolutions per day, as Space-Track publishes it.
Distances are kilometres. The semi-major axis is a **radius** measured from the
centre of the Earth; altitude is measured from the surface. Confusing the two is
a 6,378 km error, so the names say which is which.
"""

from __future__ import annotations

import math
from typing import Final

EARTH_MU_KM3_S2: Final = 398_600.4418
"""Earth's standard gravitational parameter, km^3/s^2 (WGS-84 / EGM-96)."""

EARTH_EQUATORIAL_RADIUS_KM: Final = 6_378.137
"""WGS-84 equatorial radius. Altitudes here are relative to this sphere."""

SECONDS_PER_DAY: Final = 86_400.0


def mean_motion_to_radians_per_second(mean_motion_rev_per_day: float) -> float:
    """Convert revolutions per day to radians per second."""
    if mean_motion_rev_per_day <= 0:
        raise ValueError("mean motion must be positive")
    return mean_motion_rev_per_day * 2.0 * math.pi / SECONDS_PER_DAY


def semi_major_axis_km(mean_motion_rev_per_day: float) -> float:
    """Semi-major axis from mean motion, via Kepler's third law.

    ``a = (mu / n^2)^(1/3)`` with ``n`` in radians per second.

    This is the two-body value. Space-Track's own ``SEMIMAJOR_AXIS`` agrees to
    well under a kilometre for Starlink orbits; the residual is the oblateness
    term, which is far smaller than the decay signal being measured.
    """
    n = mean_motion_to_radians_per_second(mean_motion_rev_per_day)
    return float((EARTH_MU_KM3_S2 / (n * n)) ** (1.0 / 3.0))


def mean_motion_from_semi_major_axis(semi_major_axis: float) -> float:
    """Inverse of :func:`semi_major_axis_km`, in revolutions per day."""
    if semi_major_axis <= 0:
        raise ValueError("semi-major axis must be positive")
    n_rad_s = math.sqrt(EARTH_MU_KM3_S2 / semi_major_axis**3)
    return n_rad_s * SECONDS_PER_DAY / (2.0 * math.pi)


def mean_altitude_km(mean_motion_rev_per_day: float) -> float:
    """Altitude of a circular orbit with this mean motion, above the surface."""
    return semi_major_axis_km(mean_motion_rev_per_day) - EARTH_EQUATORIAL_RADIUS_KM


def orbital_period_minutes(mean_motion_rev_per_day: float) -> float:
    """Orbital period in minutes."""
    if mean_motion_rev_per_day <= 0:
        raise ValueError("mean motion must be positive")
    return 1440.0 / mean_motion_rev_per_day


def mean_motion_rate(
    mean_motion_before: float, mean_motion_after: float, days_elapsed: float
) -> float:
    """Rate of change of mean motion, rev/day per day.

    Drag removes energy, which shrinks the orbit, which *raises* mean motion --
    so a decaying satellite has a **positive** value here. A negative value
    means the orbit is growing, which for Starlink means the thrusters are
    firing, not that the atmosphere pushed it upward.
    """
    if days_elapsed <= 0:
        raise ValueError("elapsed time must be positive")
    return (mean_motion_after - mean_motion_before) / days_elapsed


def altitude_rate_km_per_day(mean_motion_rev_per_day: float, mean_motion_rate: float) -> float:
    """Convert a mean-motion rate into an altitude rate, km/day.

    Differentiating Kepler's third law gives ``da/dt = -(2a / 3n) dn/dt``. The
    sign flips because rising mean motion means a falling orbit, so a decaying
    satellite reports a negative altitude rate.

    This is the form the research question actually wants: "metres per day of
    altitude lost" is comparable across generations and interpretable, where
    "revolutions per day per day" is neither.
    """
    a = semi_major_axis_km(mean_motion_rev_per_day)
    return -(2.0 * a) / (3.0 * mean_motion_rev_per_day) * mean_motion_rate


def apsis_altitudes_km(mean_motion_rev_per_day: float, eccentricity: float) -> tuple[float, float]:
    """(periapsis, apoapsis) altitudes above the surface, in kilometres."""
    if not 0.0 <= eccentricity < 1.0:
        raise ValueError("eccentricity must be in [0, 1)")
    a = semi_major_axis_km(mean_motion_rev_per_day)
    return (
        a * (1.0 - eccentricity) - EARTH_EQUATORIAL_RADIUS_KM,
        a * (1.0 + eccentricity) - EARTH_EQUATORIAL_RADIUS_KM,
    )


def altitude_shell(altitude_km: float, width_km: float = 25.0) -> int:
    """Bucket an altitude into a fixed-width shell, returning the lower edge.

    Neutral density falls roughly exponentially with height, so two satellites
    tens of kilometres apart feel measurably different drag. Comparing
    generations without controlling for shell would measure altitude and call it
    hardware -- this is the control, and it is deliberately coarse enough to
    keep shells populated.
    """
    if width_km <= 0:
        raise ValueError("shell width must be positive")
    return int(math.floor(altitude_km / width_km) * width_km)
