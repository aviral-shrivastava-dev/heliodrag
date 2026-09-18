-- Typed view over the bronze GP-history partitions.
--
-- Bronze is append-only and Space-Track occasionally re-issues a GP record for an
-- epoch it has already published, so the same (norad_id, epoch) can appear more
-- than once across partitions. Deduplication happens here rather than at load
-- time, which keeps bronze a faithful record of what the API returned while
-- guaranteeing downstream models a unique grain.

with source as (

    select *
    from read_parquet(
        '{{ var("gp_history_path") }}/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )

),

typed as (

    select
        cast(NORAD_CAT_ID as integer)        as norad_id,
        cast(OBJECT_NAME as varchar)         as object_name,
        cast(OBJECT_ID as varchar)           as cospar_id,
        cast(EPOCH as timestamp)             as epoch,
        cast(MEAN_MOTION as double)          as mean_motion_rev_per_day,
        cast(ECCENTRICITY as double)         as eccentricity,
        cast(INCLINATION as double)          as inclination_deg,
        cast(RA_OF_ASC_NODE as double)       as raan_deg,
        cast(ARG_OF_PERICENTER as double)    as arg_perigee_deg,
        cast(MEAN_ANOMALY as double)         as mean_anomaly_deg,
        cast(BSTAR as double)                as bstar,
        cast(MEAN_MOTION_DOT as double)      as mean_motion_dot,
        cast(SEMIMAJOR_AXIS as double)       as semimajor_axis_km,
        cast(PERIAPSIS as double)            as periapsis_km,
        cast(APOAPSIS as double)             as apoapsis_km,
        cast(REV_AT_EPOCH as bigint)         as rev_at_epoch,
        cast(EPHEMERIS_TYPE as integer)      as ephemeris_type,
        cast(epoch_date as date)             as partition_date
    from source

),

deduplicated as (

    select
        *,
        row_number() over (
            partition by norad_id, epoch
            order by partition_date desc
        ) as _recency
    from typed

)

select * exclude (_recency)
from deduplicated
where _recency = 1
