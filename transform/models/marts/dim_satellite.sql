-- One row per catalogued Starlink satellite.
--
-- The grain is the satellite, not the satellite-day: everything here is a
-- property of the hardware or of its life as a whole.

select
    norad_id,
    object_name,
    international_designator,
    object_type,
    country,

    generation,
    generation_label_source,
    generation_confidence,
    spacecraft_bus,

    launch_mass_kg,
    dry_mass_kg,
    span_m,
    area_to_mass_proxy,

    launch_date,
    decay_date,
    has_decayed,
    date_diff('day', launch_date, coalesce(decay_date, current_date)) as days_on_orbit,

    inclination_deg,
    apogee_km,
    perigee_km,
    rcs_size,
    catalogue_ingest_date

from {{ ref('int_satellite__generation_labeled') }}
