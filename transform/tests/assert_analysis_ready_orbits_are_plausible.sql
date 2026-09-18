-- Whatever the source publishes, nothing implausible may reach the headline
-- comparison. Starlink operates between roughly 340 and 570 km; the bound here
-- is deliberately wider, so it rejects only orbits no Starlink flies rather
-- than merely unusual ones.

select
    norad_id,
    epoch_date,
    mean_altitude_km,
    mean_motion,
    generation

from {{ ref('fct_daily_decay') }}

where is_analysis_ready
  and (mean_altitude_km < 100 or mean_altitude_km > 1000)
