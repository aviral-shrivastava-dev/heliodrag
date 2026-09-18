-- One row per satellite per day: the decay it showed, and the space weather it
-- was flying through. This is the table the research question is answered from.
--
-- The confounder controls the analysis needs are all present as columns rather
-- than applied as filters, so Phase 7 can condition on them explicitly and a
-- reviewer can see how much each one costs:
--
--   altitude_shell_km     -- density falls with height, so shell must be held
--   is_likely_manoeuvring -- a thrusting satellite is not measuring the air
--   has_clean_interval    -- a rate over a 5-day gap is not a daily rate
--   epoch_date            -- generation and solar cycle are collinear; the
--                            analysis needs the date to separate them

select
    decay.norad_id,
    decay.epoch_date,
    decay.epoch_at,

    satellite.generation,
    satellite.generation_confidence,
    satellite.launch_mass_kg,
    satellite.span_m,
    satellite.area_to_mass_proxy,
    satellite.launch_date,

    decay.mean_motion,
    decay.eccentricity,
    decay.bstar,
    decay.inclination_deg,
    decay.mean_altitude_km,
    decay.semimajor_axis_km,
    decay.altitude_shell_km,

    decay.mean_motion_rate_per_day,
    decay.altitude_rate_km_per_day,
    decay.interval_days,
    decay.element_sets_that_day,
    decay.is_likely_manoeuvring,
    decay.has_clean_interval,
    decay.is_rate_physically_plausible,

    weather.f10_7_sfu,
    weather.kp_mean,
    weather.kp_max,
    weather.ap_mean,
    weather.ap_max,
    weather.dst_min_nt,
    weather.dst_mean_nt,

    -- Usable for the headline comparison: a real rate, over a sane interval,
    -- from a satellite that was not under thrust, with a known generation, in
    -- an orbit a Starlink actually flies.
    --
    -- The altitude bound excludes preliminary post-launch element fits. Two
    -- were found in 2024: objects catalogued once near 3,100 km and then at
    -- 280 km three days later, a 2,900 km fall no atmosphere could produce.
    -- They are bad fits, not orbits, and Starlink operates 340-570 km.
    (
        decay.altitude_rate_km_per_day is not null
        and decay.has_clean_interval
        and not decay.is_likely_manoeuvring
        and satellite.generation <> 'unknown'
        and decay.mean_altitude_km between 100 and 1000
        and decay.is_rate_physically_plausible
    ) as is_analysis_ready

from {{ ref('int_decay__daily_rates') }} as decay
inner join {{ ref('int_satellite__generation_labeled') }} as satellite
    using (norad_id)
left join {{ ref('int_space_weather__daily') }} as weather
    using (epoch_date)
