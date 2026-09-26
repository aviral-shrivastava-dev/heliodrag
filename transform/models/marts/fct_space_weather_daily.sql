-- One row per day of space weather: the forcing, on its own.
--
-- fct_daily_decay carries the same drivers on every satellite-day, but only on
-- days a satellite was observed, and repeated once per satellite. Serving and
-- analysis both need the forcing as a series in its own right -- to plot it, to
-- mark storms, to cover days before a generation launched -- so it is a gold
-- mart rather than something every consumer re-derives from the fact.
--
-- is_storm_day uses the same Dst threshold as fct_storm_epoch, so a day shaded
-- as stormy in the explorer is a day that entered the superposed-epoch mart.

{% set storm_dst_threshold = -50 %}

select
    epoch_date,
    f10_7_sfu,
    kp_mean,
    kp_max,
    ap_mean,
    ap_max,
    dst_min_nt,
    dst_mean_nt,
    coalesce(dst_min_nt <= {{ storm_dst_threshold }}, false) as is_storm_day,
    hours_observed

from {{ ref('int_space_weather__daily') }}
