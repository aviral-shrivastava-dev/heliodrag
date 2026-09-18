-- A satellite can only lose tens of kilometres a day where the air is thick
-- enough to take them. Above ~300 km that is impossible, so a large rate up
-- there is a defect -- a bad epoch difference, a mis-parsed mean motion, or a
-- manoeuvre read as drag.
--
-- This encodes the physics rather than a blanket range, so genuine re-entries
-- at 150 km pass while an impossible rate at 550 km fails.

select
    norad_id,
    epoch_date,
    mean_altitude_km,
    altitude_rate_km_per_day,
    interval_days

from {{ ref('fct_daily_decay') }}

-- Scoped to analysis-ready rows: fct_daily_decay deliberately keeps unusable
-- ones, flagged rather than dropped, so an unconditional check here would fail
-- on data the pipeline has already excluded.
where is_analysis_ready
  and altitude_rate_km_per_day is not null
  and abs(altitude_rate_km_per_day) > 50
  and mean_altitude_km > 300
