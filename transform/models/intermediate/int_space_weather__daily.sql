-- Hourly OMNI collapsed to one row per day.
--
-- Each driver is aggregated the way it is actually used. F10.7 is a daily
-- measurement repeated across the hours, so its mean is that value. Ap is a
-- daily index, likewise. Kp is three-hourly, so both mean and max are kept --
-- a day with one severe interval is not the same as a uniformly disturbed one.
-- Dst is hourly and its MINIMUM is the meaningful figure, because a storm is
-- defined by how far the index falls.

select
    epoch_date,

    avg(f10_7_sfu)                  as f10_7_sfu,
    avg(kp_index)                   as kp_mean,
    max(kp_index)                   as kp_max,
    avg(ap_nt)                      as ap_mean,
    max(ap_nt)                      as ap_max,
    min(dst_nt)                     as dst_min_nt,
    avg(dst_nt)                     as dst_mean_nt,

    count(*)                        as hours_observed,
    count(f10_7_sfu)                as hours_with_f10_7,
    count(dst_nt)                   as hours_with_dst

from {{ ref('stg_nasa__omni') }}
group by epoch_date
