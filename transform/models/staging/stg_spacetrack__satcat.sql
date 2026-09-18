-- Catalogue snapshots, one row per satellite per ingest date. Rename and cast
-- only. Picking the current snapshot is the intermediate layer's job.

select
    cast(norad_id as bigint)      as norad_id,
    object_name,
    international_designator,
    object_type,
    country,

    cast(launch_date as date)     as launch_date,
    cast(decay_date as date)      as decay_date,
    cast(launch_year as bigint)   as launch_year,

    cast(period_minutes as double) as period_minutes,
    cast(inclination as double)    as inclination_deg,
    cast(apogee_km as double)      as apogee_km,
    cast(perigee_km as double)     as perigee_km,
    rcs_size,

    cast(ingest_date as date)      as ingest_date

from {{ source('bronze', 'satcat') }}
