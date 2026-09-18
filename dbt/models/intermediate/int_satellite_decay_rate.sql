-- Daily decay rate per satellite, plus the orbit-regime classification.
--
-- Decay rate is a centred difference over the surrounding two days, so a single
-- missing day degrades the estimate rather than dropping it. Gaps longer than
-- four days yield null rather than a slope fabricated across the hole.
--
-- Full rebuild rather than incremental: the window functions need each
-- satellite's whole series, and the input is already reduced to one row per
-- satellite-day by int_satellite_daily_state.

with daily as (

    select * from {{ ref('int_satellite_daily_state') }}

),

neighbours as (

    select
        *,
        lag(semimajor_axis_km) over w   as prev_sma_km,
        lead(semimajor_axis_km) over w  as next_sma_km,
        lag(observation_date) over w    as prev_date,
        lead(observation_date) over w   as next_date
    from daily
    window w as (partition by norad_id order by observation_date)

)

select
    * exclude (prev_sma_km, next_sma_km, prev_date, next_date),

    case
        when next_sma_km is not null and prev_sma_km is not null
             and date_diff('day', prev_date, next_date) between 1 and 4
        then (next_sma_km - prev_sma_km) / date_diff('day', prev_date, next_date)
    end as decay_rate_km_per_day,

    -- Altitude shell, for controlling the strong altitude dependence of drag.
    cast(floor(periapsis_km / 25.0) * 25 as integer) as altitude_shell_km,

    -- The catalogue is not confined to satellites quietly flying their shell: it
    -- also holds freshly deployed objects whose element sets have not converged,
    -- and satellites in their final days. Both are real records and both would
    -- corrupt a drag regression, so they are labelled, not deleted.
    case
        when periapsis_km > 700 then 'unconverged_or_transfer'
        when periapsis_km < 200 then 'reentering'
        else 'operational'
    end as orbit_regime

from neighbours
