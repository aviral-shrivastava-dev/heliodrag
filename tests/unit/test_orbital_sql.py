"""The SQL macros and science/orbital.py must agree.

The orbital formulas exist twice: once in `science/orbital.py`, scalar and
exhaustively tested, and once in `transform/macros/orbital.sql`, which runs over
millions of rows inside DuckDB. Two copies of a formula drift, and a drift here
would silently change every result in the project.

So the copies are pinned together: the constants are compared by reading the
macro file, and the arithmetic is compared by evaluating the macro's own SQL in
DuckDB against the Python function over the same inputs.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from starlink_drag.science import orbital

MACROS = Path(__file__).parents[2] / "transform" / "macros" / "orbital.sql"

MEAN_MOTIONS = [1.0, 11.0, 14.0, 15.06, 15.5, 16.4, 17.0]


def _macro_body(name: str) -> str:
    """Extract one macro's body from the .sql file."""
    text = MACROS.read_text(encoding="utf-8")
    match = re.search(
        rf"{{%\s*macro\s+{name}\s*\([^)]*\)\s*%}}(.*?){{%\s*endmacro\s*%}}",
        text,
        re.DOTALL,
    )
    assert match, f"macro {name} not found in {MACROS}"
    return match.group(1)


def _constant(name: str) -> float:
    body = _macro_body(name)
    return float(body.strip())


# -- constants -------------------------------------------------------------


def test_gravitational_parameter_matches_python() -> None:
    assert _constant("earth_mu_km3_s2") == orbital.EARTH_MU_KM3_S2


def test_earth_radius_matches_python() -> None:
    assert _constant("earth_radius_km") == orbital.EARTH_EQUATORIAL_RADIUS_KM


# -- arithmetic ------------------------------------------------------------


def _sql_semi_major_axis(mean_motion: float) -> float:
    """Evaluate the macro's own expression, with its constants substituted."""
    body = _macro_body("semi_major_axis_km")
    expression = (
        body.replace("{{ earth_mu_km3_s2() }}", repr(orbital.EARTH_MU_KM3_S2))
        .replace("{{ mean_motion }}", repr(mean_motion))
        .replace("{#- a = (mu / n^2)^(1/3), with n converted from rev/day to rad/s -#}", "")
        .strip()
    )
    with duckdb.connect() as con:
        row = con.execute(f"select {expression}").fetchone()
    assert row is not None
    return float(row[0])


@pytest.mark.parametrize("mean_motion", MEAN_MOTIONS)
def test_semi_major_axis_agrees_between_sql_and_python(mean_motion: float) -> None:
    assert _sql_semi_major_axis(mean_motion) == pytest.approx(
        orbital.semi_major_axis_km(mean_motion), rel=1e-12
    )


@pytest.mark.parametrize("mean_motion", MEAN_MOTIONS)
def test_altitude_agrees_between_sql_and_python(mean_motion: float) -> None:
    sql_altitude = _sql_semi_major_axis(mean_motion) - orbital.EARTH_EQUATORIAL_RADIUS_KM

    assert sql_altitude == pytest.approx(orbital.mean_altitude_km(mean_motion), rel=1e-12)


@pytest.mark.parametrize("mean_motion", MEAN_MOTIONS)
@pytest.mark.parametrize("rate", [-0.01, -0.0001, 0.0001, 0.002, 0.05])
def test_altitude_rate_agrees_between_sql_and_python(mean_motion: float, rate: float) -> None:
    axis = _sql_semi_major_axis(mean_motion)
    sql_rate = -(2.0 * axis) / (3.0 * mean_motion) * rate

    assert sql_rate == pytest.approx(orbital.altitude_rate_km_per_day(mean_motion, rate), rel=1e-12)


# -- the shell bucket ------------------------------------------------------


@pytest.mark.parametrize("altitude", [0.0, 137.5, 524.9, 525.0, 550.0, 561.2])
def test_altitude_shell_agrees_between_sql_and_python(altitude: float) -> None:
    with duckdb.connect() as con:
        row = con.execute(f"select floor({altitude} / 25) * 25").fetchone()
    assert row is not None

    assert int(row[0]) == orbital.altitude_shell(altitude, width_km=25.0)
