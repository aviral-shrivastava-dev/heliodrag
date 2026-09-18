-- Daily rollup of hourly space weather, to match the satellite-day grain.
--
-- ap is summed and divided by eight because it is a 3-hourly index: eight
-- readings make a day, and the daily figure conventionally reported is their
-- mean. Dst uses the minimum because storm intensity is defined by the trough.

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
