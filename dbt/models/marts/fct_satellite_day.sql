-- Analysis-ready grain: one row per satellite per UTC day.
--
-- Decay rate, orbital state, hardware generation and the space-weather
-- conditions that drove it. The derivations live in the intermediate layer; this
-- model is the join that turns them into a business entity.

with decay as (

    select * from {{ ref('int_satellite_decay_rate') }}

),

weather as (

    select * from {{ ref('int_space_weather_daily') }}

)

select
    decay.norad_id,
    decay.observation_date,

    satellite.generation,
    satellite.label_confidence,
    satellite.launch_mass_kg,
    satellite.dry_mass_kg,
    satellite.span_m,
    satellite.amr_proxy,
    satellite.in_v2_natural_experiment,

    decay.semimajor_axis_km,
    decay.periapsis_km,
    decay.apoapsis_km,
    decay.eccentricity,
    decay.inclination_deg,
    decay.bstar,
    decay.mean_motion_rev_per_day,
    decay.gp_records,
    decay.decay_rate_km_per_day,
    decay.altitude_shell_km,
    decay.orbit_regime,

    date_diff('day', satellite.launch_date, decay.observation_date) as days_since_launch,

    -- Thrust, not atmosphere. 0.5 km/day sustained climb is well above any
    -- plausible atmospheric signal at these altitudes.
    coalesce(decay.decay_rate_km_per_day > 0.5, false) as is_likely_orbit_raising,

    -- A satellite that continues to exist does not change altitude by 100 km in a
    -- day. Values beyond that are element-set artefacts, not measurements.
    coalesce(abs(decay.decay_rate_km_per_day) > 100, false) as is_implausible_decay,

    weather.f10_7_sfu,
    weather.kp_max,
    weather.kp_mean,
    weather.ap_daily,
    weather.dst_min_nt,
    weather.dst_mean_nt,
    weather.ae_max_nt,
    weather.sw_speed_km_s,
    weather.sw_pressure_npa,
    weather.imf_bz_min_nt

from decay
inner join {{ ref('dim_satellite') }} as satellite
    on decay.norad_id = satellite.norad_id
left join weather
    on decay.observation_date = weather.observation_date
