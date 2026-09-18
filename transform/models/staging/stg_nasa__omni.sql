-- Hourly space weather, as delivered. The only change made here is dividing Kp
-- by ten to put it back on its real 0-9 scale; OMNI transmits it as an integer
-- multiple of ten, which is an encoding rather than a measurement.

select
    cast(observed_at as timestamp)     as observed_at,
    cast(epoch_date as date)           as epoch_date,

    cast(f10_7_sfu as double)          as f10_7_sfu,
    cast(kp_x10 as double) / 10.0      as kp_index,
    cast(dst_nt as integer)            as dst_nt,
    cast(ap_nt as integer)             as ap_nt

from {{ source('bronze', 'omni') }}
