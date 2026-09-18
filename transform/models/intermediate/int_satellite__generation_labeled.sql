-- Every catalogued Starlink, with its hardware generation attached.
--
-- The catalogue supplies identity and orbit; the seed supplies the generation,
-- derived from GCAT's spacecraft-bus field and launch mass (see
-- science/generations.py). The join is a left join on purpose: a satellite
-- Space-Track knows about but GCAT has not yet classified must still appear,
-- labelled 'unknown', rather than vanishing from the counts.

with latest_snapshot as (

    select max(ingest_date) as ingest_date
    from {{ ref('stg_spacetrack__satcat') }}

),

catalogue as (

    select satcat.*
    from {{ ref('stg_spacetrack__satcat') }} as satcat
    inner join latest_snapshot using (ingest_date)

)

select
    catalogue.norad_id,
    catalogue.object_name,
    catalogue.international_designator,
    catalogue.object_type,
    catalogue.country,
    catalogue.launch_date,
    catalogue.decay_date,
    catalogue.inclination_deg,
    catalogue.apogee_km,
    catalogue.perigee_km,
    catalogue.rcs_size,
    catalogue.ingest_date               as catalogue_ingest_date,

    coalesce(seed.generation, 'unknown') as generation,
    coalesce(seed.label_source, 'not_in_gcat') as generation_label_source,
    coalesce(seed.label_confidence, 'none')    as generation_confidence,
    seed.bus                             as spacecraft_bus,
    seed.launch_mass_kg,
    seed.dry_mass_kg,
    seed.span_m,
    seed.area_to_mass_proxy,

    catalogue.decay_date is not null      as has_decayed

from catalogue
left join {{ ref('starlink_generation_map') }} as seed
    on catalogue.norad_id = seed.norad_id
