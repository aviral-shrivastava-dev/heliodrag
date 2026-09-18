-- One row per published element set. Rename and cast only: no joins, no
-- filtering, no business logic. Duplicates from re-run partitions survive this
-- layer on purpose and are removed in int_gp__deduplicated.

select
    cast(gp_id as bigint)                as gp_id,
    cast(norad_id as bigint)             as norad_id,
    object_name,
    object_id                            as international_designator,
    object_type,

    cast(epoch as timestamp)             as epoch_at,
    cast(epoch_date as date)             as epoch_date,

    cast(mean_motion as double)          as mean_motion_rev_per_day,
    cast(eccentricity as double)         as eccentricity,
    cast(inclination as double)          as inclination_deg,
    cast(ra_of_asc_node as double)       as raan_deg,
    cast(arg_of_pericenter as double)    as arg_of_pericenter_deg,
    cast(mean_anomaly as double)         as mean_anomaly_deg,

    cast(bstar as double)                as bstar,
    cast(mean_motion_dot as double)      as mean_motion_dot,
    cast(semimajor_axis_km as double)    as semimajor_axis_km,
    cast(period_minutes as double)       as period_minutes,
    cast(apoapsis_km as double)          as apoapsis_km,
    cast(periapsis_km as double)         as periapsis_km,

    cast(rev_at_epoch as bigint)         as rev_at_epoch,
    cast(launch_date as date)            as launch_date,
    cast(decay_date as date)             as decay_date,
    cast(creation_date as timestamp)     as created_at

from {{ source('bronze', 'gp_history') }}
