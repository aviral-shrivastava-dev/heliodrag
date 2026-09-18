{#
    Orbital conversions, in SQL.

    These mirror `src/starlink_drag/science/orbital.py`, which is the tested
    reference implementation. They exist separately because the Python version
    is scalar and this runs over millions of rows; `tests/unit/test_orbital_sql.py`
    asserts the two agree, so a change to one that is not made to the other
    fails the build.

    Constants are WGS-84 / EGM-96 and must match the Python module exactly.
#}

{% macro earth_mu_km3_s2() %}398600.4418{% endmacro %}
{% macro earth_radius_km() %}6378.137{% endmacro %}


{% macro semi_major_axis_km(mean_motion) %}
    {#- a = (mu / n^2)^(1/3), with n converted from rev/day to rad/s -#}
    pow(
        {{ earth_mu_km3_s2() }}
        / pow({{ mean_motion }} * 2 * pi() / 86400.0, 2),
        1.0 / 3.0
    )
{% endmacro %}


{% macro mean_altitude_km(mean_motion) %}
    ({{ semi_major_axis_km(mean_motion) }} - {{ earth_radius_km() }})
{% endmacro %}


{% macro altitude_rate_km_per_day(mean_motion, mean_motion_rate) %}
    {#-
        da/dt = -(2a / 3n) dn/dt. The sign flips because rising mean motion
        means a shrinking orbit, so a decaying satellite reports a negative
        altitude rate.
    -#}
    (
        -(2.0 * {{ semi_major_axis_km(mean_motion) }})
        / (3.0 * {{ mean_motion }})
        * {{ mean_motion_rate }}
    )
{% endmacro %}


{% macro altitude_shell(altitude, width=25) %}
    (floor({{ altitude }} / {{ width }}) * {{ width }})
{% endmacro %}
