-- Per-satellite, per-day orbital decay.
--
-- Space-Track publishes roughly one and a quarter element sets per satellite
-- per day at irregular times, so a "daily" rate needs a defensible choice of
-- which set represents a day. The last set of each day is used: it is closest
-- to the day boundary, which keeps the interval between consecutive days near
-- 24 hours and reduces the sampling jitter that would otherwise dominate a
-- difference of two nearly-equal mean motions.
--
-- Rates are computed against the PREVIOUS AVAILABLE day, not a fixed lag, and
-- the true gap is divided out. Gaps are common -- a satellite can go days
-- without a published set -- and treating a five-day gap as one day would
-- understate the rate fivefold.

with daily as (

    select
        norad_id,
        epoch_date,
        max(epoch_at)                                                  as epoch_at,
        arg_max(mean_motion_rev_per_day, epoch_at)                     as mean_motion,
        arg_max(eccentricity, epoch_at)                                as eccentricity,
        arg_max(bstar, epoch_at)                                       as bstar,
        arg_max(inclination_deg, epoch_at)                             as inclination_deg,
        count(*)                                                       as element_sets_that_day

    from {{ ref('int_gp__deduplicated') }}
    group by norad_id, epoch_date

),

with_previous as (

    select
        *,
        lag(mean_motion) over w   as previous_mean_motion,
        lag(epoch_at) over w      as previous_epoch_at

    from daily
    window w as (partition by norad_id order by epoch_at)

),

rates as (

    select
        norad_id,
        epoch_date,
        epoch_at,
        mean_motion,
        eccentricity,
        bstar,
        inclination_deg,
        element_sets_that_day,

        date_diff('second', previous_epoch_at, epoch_at) / 86400.0 as interval_days,

        case
            when previous_mean_motion is null then null
            when date_diff('second', previous_epoch_at, epoch_at) <= 0 then null
            else (mean_motion - previous_mean_motion)
                 / (date_diff('second', previous_epoch_at, epoch_at) / 86400.0)
        end as mean_motion_rate_per_day,

        {{ mean_altitude_km('mean_motion') }} as mean_altitude_km,
        {{ semi_major_axis_km('mean_motion') }} as semimajor_axis_km

    from with_previous

)

select
    norad_id,
    epoch_date,
    epoch_at,
    mean_motion,
    eccentricity,
    bstar,
    inclination_deg,
    element_sets_that_day,
    interval_days,
    mean_motion_rate_per_day,
    mean_altitude_km,
    semimajor_axis_km,

    case
        when mean_motion_rate_per_day is null then null
        else {{ altitude_rate_km_per_day('mean_motion', 'mean_motion_rate_per_day') }}
    end as altitude_rate_km_per_day,

    {{ altitude_shell('mean_altitude_km') }} as altitude_shell_km,

    -- A satellite under thrust is not measuring the atmosphere. Orbit raising
    -- shows as a *rising* orbit, i.e. falling mean motion, well beyond what
    -- drag could produce. This flags rather than filters: excluding rows here
    -- would hide how much data the exclusion costs.
    coalesce(mean_motion_rate_per_day < -0.0005, false) as is_likely_manoeuvring,

    -- An interval far from a day makes a difference-based rate unreliable.
    coalesce(interval_days between 0.5 and 2.0, false) as has_clean_interval,

    -- Drag cannot move an orbit arbitrarily fast, and how fast it can move one
    -- depends on where the orbit is. Below 300 km the air is thick enough to
    -- take tens of kilometres a day, and a terminal re-entry legitimately does.
    -- Above 300 km it is not: a satellite at 400 km loses of order a kilometre
    -- a day, so 70 km/day up there is thrust, not atmosphere.
    --
    -- This catches what is_likely_manoeuvring misses. That flag only sees
    -- orbits *rising*; a satellite raising its orbit in large steps shows up on
    -- alternate days as an enormous apparent *fall*, which is equally not drag.
    coalesce(
        mean_altitude_km < 300
        or abs({{ altitude_rate_km_per_day('mean_motion', 'mean_motion_rate_per_day') }}) <= 20,
        false
    ) as is_rate_physically_plausible

from rates
