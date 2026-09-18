-- Superposed-epoch input: how each generation responded around each storm.
--
-- A geomagnetic storm is identified by Dst falling below a threshold, and
-- consecutive stormy days are collapsed into one event keyed on its worst day.
-- For each event, each generation and each day offset from the peak, this gives
-- the mean decay observed.
--
-- Superposing many storms is what separates the drag response from the
-- background solar-cycle trend: the trend is slow and the storm response is
-- fast, so aligning on the peak lets the fast component be averaged out of the
-- slow one. A single storm cannot do that.

{% set storm_dst_threshold = -50 %}
{% set window_before = 5 %}
{% set window_after = 10 %}

with stormy_days as (

    select
        epoch_date,
        dst_min_nt,
        kp_max,
        ap_max,
        f10_7_sfu
    from {{ ref('int_space_weather__daily') }}
    where dst_min_nt <= {{ storm_dst_threshold }}

),

-- Consecutive stormy days belong to one storm, so they are grouped by the gap
-- between them: the running count of breaks gives each run a stable id.
runs as (

    select
        *,
        sum(case when previous_date is null
                 or date_diff('day', previous_date, epoch_date) > 1
            then 1 else 0 end) over (order by epoch_date) as storm_id
    from (
        select *, lag(epoch_date) over (order by epoch_date) as previous_date
        from stormy_days
    )

),

storms as (

    select
        storm_id,
        arg_min(epoch_date, dst_min_nt) as peak_date,
        min(dst_min_nt)                 as peak_dst_nt,
        max(kp_max)                     as peak_kp,
        max(ap_max)                     as peak_ap,
        avg(f10_7_sfu)                  as f10_7_sfu,
        count(*)                        as stormy_days
    from runs
    group by storm_id

)

select
    storms.storm_id,
    storms.peak_date,
    storms.peak_dst_nt,
    storms.peak_kp,
    storms.peak_ap,
    storms.f10_7_sfu,
    storms.stormy_days,

    decay.generation,
    date_diff('day', storms.peak_date, decay.epoch_date) as days_from_peak,
    decay.altitude_shell_km,

    count(*)                                  as observations,
    count(distinct decay.norad_id)            as satellites,
    avg(decay.altitude_rate_km_per_day)       as mean_altitude_rate_km_per_day,
    median(decay.altitude_rate_km_per_day)    as median_altitude_rate_km_per_day,
    stddev_samp(decay.altitude_rate_km_per_day) as stddev_altitude_rate_km_per_day,
    avg(decay.mean_altitude_km)               as mean_altitude_km

from storms
inner join {{ ref('fct_daily_decay') }} as decay
    on decay.epoch_date between
        storms.peak_date - interval {{ window_before }} day
        and storms.peak_date + interval {{ window_after }} day
where decay.is_analysis_ready
group by
    storms.storm_id, storms.peak_date, storms.peak_dst_nt, storms.peak_kp,
    storms.peak_ap, storms.f10_7_sfu, storms.stormy_days,
    decay.generation, date_diff('day', storms.peak_date, decay.epoch_date),
    decay.altitude_shell_km
