-- Rising mean motion must mean a falling orbit, and vice versa. If these ever
-- share a sign, the derivative in macros/orbital.sql has lost its minus sign --
-- which would silently invert every result in the project.

select
    norad_id,
    epoch_date,
    mean_motion_rate_per_day,
    altitude_rate_km_per_day

from {{ ref('fct_daily_decay') }}

where mean_motion_rate_per_day is not null
  and altitude_rate_km_per_day is not null
  and sign(mean_motion_rate_per_day) = sign(altitude_rate_km_per_day)
  and mean_motion_rate_per_day <> 0
