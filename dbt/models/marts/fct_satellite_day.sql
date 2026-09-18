-- Analysis-ready grain: one row per satellite per UTC day.
--
-- This is the table the project exists to produce. It carries the decay rate
-- (d(semi-major axis)/dt) alongside the space-weather state that drove it, and
-- the hardware generation that modulates the response.
--
-- Method notes
-- ------------
-- Space-Track publishes several GP records per satellite per day and they are
-- noisy at the metre level. Taking the daily *median* semi-major axis rather
-- than the last value suppresses that jitter without smoothing across days,
-- which matters because storm response is a day-scale signal.
--
-- Decay rate is a centred difference over the surrounding two days, so a single
-- missing day degrades the estimate rather than dropping it.
--
-- Satellites climbing to their operational shell have a large *positive* da/dt
-- driven by thrust, not atmosphere. Those days are flagged, not deleted -- an
-- analysis that silently drops them would bias the sample toward satellites that
-- finished orbit-raising early.

with daily_state as (

    select
        norad_id,
        cast(epoch as date)                      as observation_date,
        median(semimajor_axis_km)                as semimajor_axis_km,
        median(periapsis_km)                     as periapsis_km,
        median(apoapsis_km)                      as apoapsis_km,
        median(eccentricity)                     as eccentricity,
        median(inclination_deg)                  as inclination_deg,
        median(bstar)                            as bstar,
        median(mean_motion_rev_per_day)          as mean_motion_rev_per_day,
        count(*)                                 as gp_records
    from {{ ref('stg_gp_history') }}
    group by 1, 2

),

with_rates as (

    select
        *,
        lag(semimajor_axis_km) over w   as prev_sma_km,
        lead(semimajor_axis_km) over w  as next_sma_km,
        lag(observation_date) over w    as prev_date,
        lead(observation_date) over w   as next_date
    from daily_state
    window w as (partition by norad_id order by observation_date)

),

decay as (

    select
        *,
        case
            when next_sma_km is not null and prev_sma_km is not null
                 and date_diff('day', prev_date, next_date) between 1 and 4
            then (next_sma_km - prev_sma_km) / date_diff('day', prev_date, next_date)
        end as decay_rate_km_per_day
    from with_rates

),

space_weather_daily as (

    select
        cast(observed_at as date) as observation_date,
        avg(f10_7_sfu)            as f10_7_sfu,
        max(kp)                   as kp_max,
        avg(kp)                   as kp_mean,
        sum(ap) / 8.0             as ap_daily,
        min(dst_nt)               as dst_min_nt,
        avg(dst_nt)               as dst_mean_nt,
        max(ae_nt)                as ae_max_nt,
        avg(sw_speed_km_s)        as sw_speed_km_s,
        avg(sw_pressure_npa)      as sw_pressure_npa,
        min(imf_bz_gsm_nt)        as imf_bz_min_nt
    from {{ ref('stg_omni') }}
    group by 1

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

    -- Altitude shell, for controlling the strong altitude dependence of drag.
    cast(floor(decay.periapsis_km / 25.0) * 25 as integer) as altitude_shell_km,

    date_diff('day', satellite.launch_date, decay.observation_date) as days_since_launch,

    -- Thrust, not atmosphere. 0.5 km/day sustained climb is well above any
    -- plausible atmospheric signal at these altitudes.
    coalesce(decay.decay_rate_km_per_day > 0.5, false) as is_likely_orbit_raising,

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
left join space_weather_daily as weather
    on decay.observation_date = weather.observation_date
