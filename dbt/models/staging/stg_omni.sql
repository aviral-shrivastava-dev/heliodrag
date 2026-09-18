-- Typed view over bronze OMNI, converting the server's fill sentinels to nulls.
--
-- Two things bronze deliberately did not do, done here:
--   1. Fill values (999.9, 99, 99999, ...) become null. Leaving them in place
--      would silently poison any average: a single 99999 Dst moves a monthly
--      mean by thousands of nT.
--   2. KP1800 is Kp x 10 as an integer (Kp=1- is stored as 7), so it is divided
--      back into real Kp units here.

with source as (

    select *
    from {{ source('bronze', 'omni') }}

)

select
    cast(time as timestamp) as observed_at,

    nullif(cast(F10_INDEX1800 as double), 999.9)   as f10_7_sfu,
    nullif(cast(KP1800 as double), 99) / 10.0      as kp,
    nullif(cast(AP_INDEX1800 as double), 999)      as ap,
    nullif(cast(DST1800 as double), 99999)         as dst_nt,
    nullif(cast(AE1800 as double), 9999)           as ae_nt,
    nullif(cast(R1800 as double), 999)             as sunspot_number,
    nullif(cast(Pressure1800 as double), 99.99)    as sw_pressure_npa,
    nullif(cast(V1800 as double), 9999.0)          as sw_speed_km_s,
    nullif(cast(BZ_GSM1800 as double), 999.9)      as imf_bz_gsm_nt

from source
