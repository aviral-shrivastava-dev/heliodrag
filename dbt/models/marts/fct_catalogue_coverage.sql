-- Audit: how much orbital data is lost to the satellite dimension each day.
--
-- `fct_satellite_day` inner-joins to dim_satellite, so any satellite Space-Track
-- reports that GCAT has not yet catalogued is dropped. That loss is not random:
-- GCAT curates new launches with a lag of roughly ten weeks, so the satellites
-- missing on any given day are always the *newest* ones, which in the current era
-- means v2-mini-opt. Silently inner-joining would therefore bias the newest
-- variant's sample toward its older members.
--
-- This model makes that loss a measured quantity rather than an invisible one.
-- The accompanying test fails when coverage degrades beyond the level explained
-- by normal catalogue lag.

with observed as (

    select
        observation_date,
        norad_id
    from {{ ref('int_satellite_daily_state') }}

),

joined as (

    select
        observed.observation_date,
        observed.norad_id,
        satellite.norad_id is not null as is_catalogued,
        satellite.generation
    from observed
    left join {{ ref('dim_satellite') }} as satellite
        on observed.norad_id = satellite.norad_id

)

select
    observation_date,
    count(*)                                              as satellites_observed,
    count(*) filter (where is_catalogued)                 as satellites_catalogued,
    count(*) filter (where not is_catalogued)             as satellites_unmatched,
    round(100.0 * count(*) filter (where is_catalogued) / count(*), 2) as coverage_pct
from joined
group by 1
