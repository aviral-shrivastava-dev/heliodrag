{{
    config(
        materialized = 'incremental',
        unique_key = ['norad_id', 'observation_date'],
        incremental_strategy = 'delete+insert',
    )
}}

-- Collapse many GP records per satellite per day into one daily state.
--
-- This is the expensive step: it scans every bronze partition. Making it
-- incremental means a daily run touches a handful of partitions instead of
-- re-reading the whole history.
--
-- Why a lookback rather than a plain watermark: Space-Track republishes revised
-- element sets for epochs it has already issued, so a day that was loaded a week
-- ago can legitimately change. A strict `> max(observation_date)` filter would
-- never revisit it and the correction would be silently lost. Re-processing a
-- trailing window and deleting-then-inserting those days picks the revision up.
--
-- Median rather than last-value: Space-Track elements jitter at the metre level
-- between records, and the median suppresses that without smoothing across days,
-- which matters because storm response is a day-scale signal.

with gp as (

    select *
    from {{ ref('stg_gp_history') }}

    {% if is_incremental() %}
    where cast(epoch as date) >= (
        select max(observation_date) - interval {{ var('gp_lookback_days', 7) }} day
        from {{ this }}
    )
    {% endif %}

)

select
    norad_id,
    cast(epoch as date)                 as observation_date,
    median(semimajor_axis_km)           as semimajor_axis_km,
    median(periapsis_km)                as periapsis_km,
    median(apoapsis_km)                 as apoapsis_km,
    median(eccentricity)                as eccentricity,
    median(inclination_deg)             as inclination_deg,
    median(bstar)                       as bstar,
    median(mean_motion_rev_per_day)     as mean_motion_rev_per_day,
    count(*)                            as gp_records
from gp
group by 1, 2
