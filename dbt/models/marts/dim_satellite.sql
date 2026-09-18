-- Satellite dimension: one row per Starlink payload, carrying the hardware
-- generation every downstream analysis stratifies by.
--
-- Built by `starlink_drag.build` from GCAT rather than in SQL, because the
-- classification needs the mass-banding logic and its validation suite. This
-- model types it and exposes it to the warehouse.

with source as (

    select *
    from read_parquet('{{ var("interim_path") }}/starlink_generation_map.parquet')

)

select
    cast(norad_id as integer)            as norad_id,
    cast(jcat as varchar)                as jcat,
    cast(name as varchar)                as satellite_name,
    cast(launch_tag as varchar)          as launch_tag,
    cast(bus as varchar)                 as bus,

    cast(generation as varchar)          as generation,
    cast(label_source as varchar)        as label_source,
    cast(label_confidence as varchar)    as label_confidence,

    cast(mass_kg as double)              as launch_mass_kg,
    cast(dry_mass_kg as double)          as dry_mass_kg,
    cast(span_m as double)               as span_m,

    -- Wingspan, not drag area: see the caveat in README. Useful for comparing
    -- variants to each other, not as an absolute ballistic coefficient.
    cast(crude_amr_proxy_span2_over_mass as double) as amr_proxy,

    cast(launch_date as date)            as launch_date,
    cast(decay_date as date)             as decay_date,
    cast(is_decayed as boolean)          as is_decayed,

    -- The V2 Mini variants share an identical 29 m span while differing in mass
    -- by 1.67x, and flew concurrently for 22-32 months. That makes them the only
    -- generation comparison where geometry and epoch are both controlled, so it
    -- is flagged here rather than rediscovered in every downstream query.
    generation in ('v2-mini', 'v2-mini-opt', 'v2-mini-dtc') as in_v2_natural_experiment

from source
where norad_id is not null
