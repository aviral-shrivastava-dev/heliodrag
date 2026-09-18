"""Orbital mechanics, checked against values known independently.

A derived decay rate is only trustworthy if the conversions under it are. These
tests check against things that hold regardless of this code: the known radius of
a geostationary orbit, the known altitude of the ISS, the identities the formulae
must satisfy, and -- where a real capture is present locally -- Space-Track's own
derived fields.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from starlink_drag.science import orbital

FIXTURES = Path(__file__).parent.parent / "fixtures" / "spacetrack"


def _records() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(
        (FIXTURES / "gp_history_sample.json").read_text(encoding="utf-8")
    )
    # The last record is deliberately unphysical; exclude it here.
    return rows[:-1]


# -- agreement with an independently derived element set --------------------
#
# The committed fixture's derived fields were computed by a separate inline
# implementation when the fixture was built, deliberately not by importing this
# module. Agreement therefore means two implementations of Kepler's third law
# concur -- weaker than checking against Space-Track, which is done below
# against the real capture when one is present.


def test_semi_major_axis_agrees_with_the_element_sets_own_value() -> None:
    for record in _records():
        computed = orbital.semi_major_axis_km(float(record["MEAN_MOTION"]))
        published = float(record["SEMIMAJOR_AXIS"])

        assert computed == pytest.approx(published, abs=1.0), record["NORAD_CAT_ID"]


def test_period_agrees_with_the_element_sets_own_value() -> None:
    for record in _records():
        computed = orbital.orbital_period_minutes(float(record["MEAN_MOTION"]))

        assert computed == pytest.approx(float(record["PERIOD"]), abs=0.05)


def test_apsis_altitudes_agree_with_the_element_sets_own_values() -> None:
    for record in _records():
        periapsis, apoapsis = orbital.apsis_altitudes_km(
            float(record["MEAN_MOTION"]), float(record["ECCENTRICITY"])
        )

        assert periapsis == pytest.approx(float(record["PERIAPSIS"]), abs=1.5)
        assert apoapsis == pytest.approx(float(record["APOAPSIS"]), abs=1.5)


# -- agreement with Space-Track itself (real data, local only) --------------

# Absolute: every test runs from an empty temporary directory (see conftest),
# so a relative path would resolve to nothing.
REAL_CAPTURE = (
    Path(__file__).parents[2] / "data" / "fixtures" / "spacetrack" / "gp_history_sample.json"
)


@pytest.mark.skipif(
    not REAL_CAPTURE.exists(),
    reason="no real Space-Track capture; it is gitignored and absent in CI",
)
def test_agrees_with_space_tracks_own_derived_fields() -> None:
    """The real cross-check, against values Space-Track computed itself.

    Runs only where a genuine capture exists. It cannot run in CI because the
    data is not redistributable, which is why the committed fixture carries
    substituted values -- see tests/fixtures/README.md.
    """
    records = json.loads(REAL_CAPTURE.read_text(encoding="utf-8"))
    assert records, "capture is empty"

    for record in records:
        mean_motion = float(record["MEAN_MOTION"])
        assert orbital.semi_major_axis_km(mean_motion) == pytest.approx(
            float(record["SEMIMAJOR_AXIS"]), abs=1.0
        )
        assert orbital.orbital_period_minutes(mean_motion) == pytest.approx(
            float(record["PERIOD"]), abs=0.05
        )
        periapsis, apoapsis = orbital.apsis_altitudes_km(mean_motion, float(record["ECCENTRICITY"]))
        assert periapsis == pytest.approx(float(record["PERIAPSIS"]), abs=1.5)
        assert apoapsis == pytest.approx(float(record["APOAPSIS"]), abs=1.5)


# -- agreement with known orbits -------------------------------------------


def test_the_iss_comes_out_at_roughly_its_known_altitude() -> None:
    """The ISS circles about 15.5 times a day at roughly 420 km."""
    assert orbital.mean_altitude_km(15.5) == pytest.approx(420, abs=15)


def test_a_geostationary_orbit_comes_out_at_its_known_radius() -> None:
    """One revolution per sidereal day must give 42,164 km."""
    sidereal_revs_per_day = 86_400.0 / 86_164.0

    assert orbital.semi_major_axis_km(sidereal_revs_per_day) == pytest.approx(42_164, abs=5)


def test_starlink_shell_altitude_is_about_550_km() -> None:
    assert orbital.mean_altitude_km(15.06) == pytest.approx(547, abs=10)


# -- identities the formulae must satisfy ----------------------------------


@pytest.mark.parametrize("mean_motion", [1.0, 12.5, 15.06, 16.4, 19.0])
def test_semi_major_axis_round_trips(mean_motion: float) -> None:
    axis = orbital.semi_major_axis_km(mean_motion)

    assert orbital.mean_motion_from_semi_major_axis(axis) == pytest.approx(mean_motion)


def test_a_circular_orbit_has_equal_apsides() -> None:
    periapsis, apoapsis = orbital.apsis_altitudes_km(15.06, 0.0)

    assert periapsis == pytest.approx(apoapsis)
    assert periapsis == pytest.approx(orbital.mean_altitude_km(15.06))


def test_higher_mean_motion_means_a_lower_orbit() -> None:
    assert orbital.mean_altitude_km(15.5) < orbital.mean_altitude_km(15.0)


# -- decay -----------------------------------------------------------------


def test_a_decaying_satellite_has_a_positive_mean_motion_rate() -> None:
    """Drag shrinks the orbit, which raises mean motion."""
    rate = orbital.mean_motion_rate(15.060, 15.064, days_elapsed=2.0)

    assert rate > 0
    assert rate == pytest.approx(0.002)


def test_a_decaying_satellite_loses_altitude() -> None:
    """Rising mean motion must convert to a negative altitude rate."""
    rate = orbital.altitude_rate_km_per_day(15.06, mean_motion_rate=0.002)

    assert rate < 0


def test_the_altitude_rate_matches_a_direct_difference() -> None:
    """da/dt from the derivative must agree with differencing two altitudes."""
    before, after, days = 15.0600, 15.0640, 1.0
    rate = orbital.mean_motion_rate(before, after, days)

    derivative = orbital.altitude_rate_km_per_day(before, rate)
    difference = (orbital.mean_altitude_km(after) - orbital.mean_altitude_km(before)) / days

    assert derivative == pytest.approx(difference, rel=1e-3)


def test_a_manoeuvring_satellite_reports_a_rising_orbit() -> None:
    """Orbit raising shows as falling mean motion, which must not be read as
    the atmosphere pushing a satellite upward."""
    rate = orbital.mean_motion_rate(15.064, 15.060, days_elapsed=2.0)

    assert rate < 0
    assert orbital.altitude_rate_km_per_day(15.064, rate) > 0


# -- shells ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("altitude", "expected"),
    [(550.0, 550), (549.9, 525), (525.0, 525), (0.0, 0), (-5.0, -25)],
)
def test_altitude_shells_bucket_to_the_lower_edge(altitude: float, expected: int) -> None:
    assert orbital.altitude_shell(altitude, width_km=25.0) == expected


def test_two_satellites_in_the_same_shell_share_a_bucket() -> None:
    assert orbital.altitude_shell(551.0) == orbital.altitude_shell(560.0)


# -- rejected input --------------------------------------------------------


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_non_positive_mean_motion_is_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        orbital.semi_major_axis_km(bad)
    with pytest.raises(ValueError):
        orbital.orbital_period_minutes(bad)


def test_elapsed_time_must_be_positive() -> None:
    with pytest.raises(ValueError):
        orbital.mean_motion_rate(15.0, 15.1, days_elapsed=0.0)


@pytest.mark.parametrize("bad", [-0.1, 1.0, 1.5])
def test_eccentricity_outside_a_closed_orbit_is_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        orbital.apsis_altitudes_km(15.06, bad)


def test_shell_width_must_be_positive() -> None:
    with pytest.raises(ValueError):
        orbital.altitude_shell(550.0, width_km=0.0)


def test_no_function_here_performs_io() -> None:
    """science/ is pure by contract; this is a cheap guard on that."""
    source = (Path(orbital.__file__)).read_text(encoding="utf-8")

    for forbidden in ("import requests", "import httpx", "open(", "Path(", "print("):
        assert forbidden not in source, forbidden
    assert math is not None
